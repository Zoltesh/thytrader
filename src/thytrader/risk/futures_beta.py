"""BTC-beta with futures: the futures-scope cap and opt-in base-unit netting (ADR 0129 §6).

**Futures-scope cap.** ``futures.max_btc_beta_exposure_fraction`` bounds Σ |futures notional|
x β over the mode's risk-bearing futures books plus the proposed entry, against the futures
capital (``paper_capital_usd``). β of a contract is the estimate ADR 0125 computes for
``<underlying>-<policy quote>``. A book whose underlying or β is unknown denies.

**Netting (``beta_netting: net_by_underlying``).** For the spot policy-quote β cap only, a
managed futures position in the same (paper) mode nets against same-mode spot inventory of
the same underlying *in base units*: net = spot base (signed) + futures base (signed). The
net is valued at the spot mark of ``<underlying>-<policy quote>`` times β, so no USD amount
enters the policy-quote figure. Each underlying fails closed to gross unless every futures
book on it is bound, its funding is current and its mark is known, and the spot mark exists.
Netting only ever lowers the spot figure (a same-direction futures leg is not added), and it
never relaxes the futures scope's own checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.risk.beta import BetaUnavailable, fresh_beta
from thytrader.risk.gate_common import _deny
from thytrader.risk.models import RiskReasonCode
from thytrader.trading.exposure import product_exposure, risk_bearing_snapshots
from thytrader.trading.models import (
    DeploymentMode,
    PositionSide,
    resolved_product_id,
    snapshot_positions,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime
    from uuid import UUID

    from thytrader.risk.beta import BetaEvidence
    from thytrader.risk.gate_common import ProposedEntry
    from thytrader.risk.models import RiskPolicyDefinition, RiskVerdict
    from thytrader.trading.models import DeploymentSnapshot

_ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class FuturesLegs:
    """The bound futures books of one mode: underlying per book and whose funding is current.

    A book absent from ``underlyings`` has no readable binding; a book absent from
    ``funding_current`` has an overdue funding hour. Both make its evidence unknown.
    """

    underlyings: Mapping[UUID, str] = field(default_factory=dict)
    funding_current: frozenset[UUID] = frozenset()


def futures_beta_products(legs: FuturesLegs | None, quote: str) -> tuple[str, ...]:
    """The spot products whose β the futures-scope cap reads (``<underlying>-<quote>``)."""
    if legs is None:
        return ()
    return tuple(sorted({f"{underlying}-{quote}" for underlying in legs.underlyings.values()}))


def futures_beta_verdict(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    proposed: ProposedEntry,
    proposing_underlying: str,
    snapshots: Sequence[DeploymentSnapshot],
    legs: FuturesLegs | None,
    beta: BetaEvidence | None,
    capital: Decimal,
    as_of: datetime | None,
) -> RiskVerdict | None:
    """Deny when the futures scope's β-weighted gross exposure would exceed its cap."""
    futures = policy.futures
    if futures is None or futures.max_btc_beta_exposure_fraction is None:
        return None
    quote = policy.quote_currency
    if beta is None or as_of is None or legs is None:
        return _deny(
            RiskReasonCode.BTC_BETA_UNAVAILABLE,
            "Futures BTC beta evidence was not loaded; new futures entries blocked.",
        )
    weighted = _ZERO
    terms: list[tuple[str, Decimal]] = [(proposing_underlying, abs(proposed.notional))]
    for book in risk_bearing_snapshots(snapshots, mode):
        product_id = book.deployment.product_id
        if not is_futures_product_id(product_id):
            continue
        exposure = abs(product_exposure(book, product_id))
        if exposure == 0:
            continue
        underlying = legs.underlyings.get(book.deployment.id)
        if underlying is None:
            return _deny(
                RiskReasonCode.BTC_BETA_UNAVAILABLE,
                f"Futures book {book.deployment.id} has no readable contract binding; its "
                "beta is unknown and new futures entries are blocked.",
            )
        terms.append((underlying, exposure))
    for underlying, exposure in terms:
        reference = f"{underlying}-{quote}"
        result = fresh_beta(beta.result_for(reference), as_of=as_of)
        if isinstance(result, BetaUnavailable):
            return _deny(
                RiskReasonCode.BTC_BETA_UNAVAILABLE,
                f"BTC beta unavailable for {reference}: {result.describe()}; new futures "
                "entries blocked.",
            )
        weighted += exposure * max(result.beta, _ZERO)
    cap = capital * Decimal(futures.max_btc_beta_exposure_fraction)
    if capital > 0 and weighted <= cap:
        return None
    return _deny(
        RiskReasonCode.BTC_BETA_EXPOSURE_EXCEEDED,
        f"Futures BTC-beta exposure {weighted} USD would exceed the cap {cap} USD "
        f"({futures.max_btc_beta_exposure_fraction} x futures capital {capital} USD).",
    )


def netting_adjustment(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    proposed: ProposedEntry,
    occupied: Sequence[DeploymentSnapshot],
    snapshots: Sequence[DeploymentSnapshot],
    legs: FuturesLegs | None,
    betas: Mapping[str, Decimal],
    marks: Mapping[str, Decimal],
) -> Decimal:
    """How much base-unit netting lowers the spot β figure (zero or negative).

    ``occupied`` is the spot gate's same-quote book set and ``betas`` the β already
    checked for every product the cap sums. Only underlyings with a futures leg whose
    evidence is fully known are netted; everything else stays gross.
    """
    futures = policy.futures
    if (
        futures is None
        or not futures.nets_by_underlying
        or mode is not DeploymentMode.PAPER
        or legs is None
        or proposed.side is None
    ):
        return _ZERO
    quote = policy.quote_currency
    hedges = _futures_base_by_underlying(snapshots, mode=mode, legs=legs, marks=marks)
    adjustment = _ZERO
    for underlying, futures_base in hedges.items():
        spot_product = f"{underlying}-{quote}"
        beta = betas.get(spot_product)
        mark = marks.get(spot_product)
        if futures_base == 0 or beta is None or mark is None or mark <= 0:
            continue
        spot_base, gross = _spot_leg(occupied, spot_product)
        if proposed.product_id == spot_product and proposed.quantity is not None:
            signed = -proposed.quantity if proposed.side == "short" else proposed.quantity
            spot_base += signed
            gross += abs(proposed.notional)
        if gross == 0:
            continue
        net = abs(spot_base + futures_base) * mark
        adjustment += min(net - gross, _ZERO) * beta
    return adjustment


def _futures_base_by_underlying(
    snapshots: Sequence[DeploymentSnapshot],
    *,
    mode: DeploymentMode,
    legs: FuturesLegs,
    marks: Mapping[str, Decimal],
) -> dict[str, Decimal]:
    """Signed futures base per underlying; an underlying with any unknown leg is dropped."""
    totals: dict[str, Decimal] = {}
    unknown: set[str] = set()
    for book in risk_bearing_snapshots(snapshots, mode):
        product_id = book.deployment.product_id
        if not is_futures_product_id(product_id):
            continue
        underlying = legs.underlyings.get(book.deployment.id)
        if underlying is None:
            # An unbound book's underlying is unknown, so no underlying may net past it.
            return {}
        signed = _signed_base(book, product_id)
        if signed == 0:
            continue
        if book.deployment.id not in legs.funding_current or product_id not in marks:
            unknown.add(underlying)
            continue
        totals[underlying] = totals.get(underlying, _ZERO) + signed
    return {underlying: base for underlying, base in totals.items() if underlying not in unknown}


def _spot_leg(occupied: Sequence[DeploymentSnapshot], spot_product: str) -> tuple[Decimal, Decimal]:
    """Signed spot base and gross position cost of one product across the spot books."""
    base = _ZERO
    gross = _ZERO
    for book in occupied:
        for position in snapshot_positions(book):
            if resolved_product_id(position.product_id, book.deployment) != spot_product:
                continue
            signed = (
                -position.quantity if position.side is PositionSide.SHORT else position.quantity
            )
            base += signed
            gross += position.quantity * position.entry_price
    return base, gross


def _signed_base(book: DeploymentSnapshot, product_id: str) -> Decimal:
    """Signed base quantity a futures book holds."""
    total = _ZERO
    for position in snapshot_positions(book):
        if resolved_product_id(position.product_id, book.deployment) != product_id:
            continue
        total += -position.quantity if position.side is PositionSide.SHORT else position.quantity
    return total
