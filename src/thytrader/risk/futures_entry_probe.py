"""Minimum whole-contract admission probes shared by read-only futures consumers."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, DecimalException, Inexact, localcontext
from typing import TYPE_CHECKING

from thytrader.risk.futures_entry import futures_capital, futures_caps_verdict
from thytrader.risk.futures_live import (
    live_futures_entry_verdict,
    live_futures_opt_in_verdict,
    live_futures_standing_verdict,
)
from thytrader.risk.gate_common import ProposedEntry, _deny
from thytrader.risk.models import RiskReasonCode
from thytrader.trading.fill_ledger import unprojected_inventory_products, unsettled_fill_evidence
from thytrader.trading.models import DeploymentMode

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from thytrader.risk.futures_live import FuturesVenueEvidence
    from thytrader.risk.models import RiskPolicyDefinition, RiskVerdict
    from thytrader.trading.futures_book import FuturesBookState
    from thytrader.trading.models import DeploymentSnapshot


@dataclass(frozen=True, slots=True)
class LiveFuturesProbe:
    """Loaded read-only state and a timestamped positive mark; no order authority."""

    state: FuturesBookState
    price: Decimal | None


def live_probe_verdicts(
    policy: RiskPolicyDefinition,
    *,
    book: DeploymentSnapshot,
    probe: LiveFuturesProbe | None,
    snapshots: Sequence[DeploymentSnapshot],
    venue: FuturesVenueEvidence | None,
    now: datetime,
) -> tuple[RiskVerdict, ...]:
    """Return both direction denials, or empty if at least one minimum entry can pass.

    The gate's §5 caps (order contracts, gross exposure, funding rate) run only once every
    evidence and venue check admits the probe, so a capacity denial never hides missing
    evidence (and never lets its alert clear).
    """
    verdicts = _evidence_verdicts(
        policy, book=book, probe=probe, snapshots=snapshots, venue=venue, now=now
    )
    if verdicts or probe is None:
        return verdicts
    price = probe.price
    priced = price is not None and price.is_finite() and price > 0
    caps = _caps_verdict(policy, probe=probe, snapshots=snapshots, price=price if priced else None)
    return () if caps is None else (caps,)


def _evidence_verdicts(
    policy: RiskPolicyDefinition,
    *,
    book: DeploymentSnapshot,
    probe: LiveFuturesProbe | None,
    snapshots: Sequence[DeploymentSnapshot],
    venue: FuturesVenueEvidence | None,
    now: datetime,
) -> tuple[RiskVerdict, ...]:
    """Every evidence, venue and per-direction check of the probe, before the §5 caps.

    This is an advisory one-contract probe, not a strategy signal or a guarantee of a
    future order. Missing evidence denies; protective exits never consume this helper.
    The probe is priced at an open position's venue mark. A flat book has no position and so
    no mark: its probe runs every check that needs no price, including the price-independent
    part of the margin check, and leaves sizing the entry's own margin to the real entry. A
    book holding inventory (a position, or fills not yet projected or settled) without a
    fresh mark is unknown evidence and denies.
    """
    opt_in = live_futures_opt_in_verdict(policy, book.deployment.product_id)
    if opt_in is not None:
        return (opt_in,)
    if probe is None:
        return (
            _deny(
                RiskReasonCode.FUTURES_CONTRACT_UNBOUND,
                "Live futures binding and margin evidence are unavailable.",
            ),
        )
    margin = probe.state.margin
    price = probe.price
    priced = price is not None and price.is_finite() and price > 0
    block = probe.state.live_entry_block()
    if block is not None:
        return (_deny(RiskReasonCode(block[0]), block[1]),)
    if not priced and _holds_inventory(book):
        return (
            _deny(
                RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN,
                "A fresh positive mark is required for a minimum one-contract admission probe.",
            ),
        )
    if margin is None:
        return (_deny(RiskReasonCode.FUTURES_MARGIN_UNKNOWN, "Margin terms are unknown."),)
    if price is None or not priced:
        return _standing_verdicts(
            policy,
            book=book,
            probe=probe,
            contract_size=margin.contract_size,
            snapshots=snapshots,
            venue=venue,
            now=now,
        )
    try:
        with localcontext() as context:
            context.traps[Inexact] = True
            notional = margin.contract_size * price
    except DecimalException:
        return (
            _deny(
                RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN,
                "Minimum entry notional cannot be represented exactly.",
            ),
        )
    results: list[RiskVerdict] = []
    for side in ("long", "short"):
        verdict = live_futures_entry_verdict(
            policy,
            proposed=ProposedEntry(
                product_id=book.deployment.product_id,
                strategy_id=book.deployment.strategy_id,
                quantity=margin.contract_size,
                notional=notional,
                side=side,
            ),
            book=book,
            state=probe.state,
            snapshots=snapshots,
            venue=venue,
            as_of=now,
        )
        if verdict is None:
            return ()
        results.append(verdict)
    return tuple(results)


def _caps_verdict(
    policy: RiskPolicyDefinition,
    *,
    probe: LiveFuturesProbe,
    snapshots: Sequence[DeploymentSnapshot],
    price: Decimal | None,
) -> RiskVerdict | None:
    """The gate's §5 caps for a one-contract probe: order contracts, exposure, funding rate.

    Without a price the probe's notional is zero, so only exposure already over the cap
    blocks; the entry's own size is left to the real entry, as for margin.
    """
    margin = probe.state.margin
    size = None if margin is None else margin.contract_size
    try:
        with localcontext() as context:
            context.traps[Inexact] = True
            notional = Decimal(0) if price is None or size is None else size * price
    except DecimalException:
        return None
    return futures_caps_verdict(
        policy,
        mode=DeploymentMode.LIVE,
        state=probe.state,
        notional=notional,
        quantity=size,
        snapshots=snapshots,
        capital=futures_capital(policy, DeploymentMode.LIVE),
    )


def _standing_verdicts(
    policy: RiskPolicyDefinition,
    *,
    book: DeploymentSnapshot,
    probe: LiveFuturesProbe,
    contract_size: Decimal,
    snapshots: Sequence[DeploymentSnapshot],
    venue: FuturesVenueEvidence | None,
    now: datetime,
) -> tuple[RiskVerdict, ...]:
    """A flat book's probe without a price; no check it runs depends on the side."""
    verdict = live_futures_standing_verdict(
        policy,
        proposed=ProposedEntry(
            product_id=book.deployment.product_id,
            strategy_id=book.deployment.strategy_id,
            quantity=contract_size,
            notional=Decimal(0),
        ),
        book=book,
        state=probe.state,
        snapshots=snapshots,
        venue=venue,
        as_of=now,
    )
    return () if verdict is None else (verdict,)


def _holds_inventory(book: DeploymentSnapshot) -> bool:
    """A position, or fills whose inventory is not projected or settled yet."""
    return (
        book.position is not None
        or bool(book.positions)
        or bool(unprojected_inventory_products(book))
        or unsettled_fill_evidence(book)
    )
