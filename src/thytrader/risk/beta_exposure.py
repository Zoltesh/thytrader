"""BTC-beta-weighted exposure cap (ADR 0125).

Bounds Σ |product exposure| * β over the entry's same-quote risk-bearing books, plus the
proposed notional * its β, by ``max_btc_beta_exposure_fraction`` of the capital base and,
for live, the absolute ``max_btc_beta_exposure_quote``. β is measured against
``BTC-<quote>`` (``risk.beta``). Exposure is the same measure as the account exposure cap
(position cost plus working entries) and is counted gross: a short never hedges a long.
A missing, unloaded or stale β for the proposed product or any held product denies with
``BTC_BETA_UNAVAILABLE``; no default β is ever assumed. An unset cap needs no evidence.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING

from thytrader.market_data.products import is_spot_product_id
from thytrader.risk.beta import BetaUnavailable, beta_reference, fresh_beta
from thytrader.risk.breakers import quote_scoped_snapshots
from thytrader.risk.entry_limits import _capital_base
from thytrader.risk.gate_common import _book_products, _deny
from thytrader.risk.models import RiskReasonCode
from thytrader.trading.exposure import product_exposure, risk_bearing_snapshots
from thytrader.trading.models import DeploymentMode

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from thytrader.risk.beta import BetaEvidence
    from thytrader.risk.gate_common import ProposedEntry
    from thytrader.risk.models import RiskPolicyDefinition, RiskVerdict
    from thytrader.trading.models import DeploymentSnapshot

_SHOWN = Decimal("0.01")


def beta_cap_applies(policy: RiskPolicyDefinition, mode: DeploymentMode) -> bool:
    """True when a β cap binds entries in ``mode``; only then is β evidence needed.

    The fraction binds paper and live. The absolute quote cap binds live only, like
    ``max_portfolio_exposure_quote``.
    """
    if policy.max_btc_beta_exposure_fraction is not None:
        return True
    return policy.max_btc_beta_exposure_quote is not None and mode is DeploymentMode.LIVE


def beta_products(
    snapshots: Sequence[DeploymentSnapshot], *, mode: DeploymentMode, product_id: str
) -> tuple[str, ...]:
    """Return the products whose β the cap needs for this entry, proposed product first.

    These are the proposed product and every product with exposure in the same-quote
    risk-bearing books the gate sums, so a loader can read exactly what the gate checks.
    """
    books, incomplete = quote_scoped_snapshots(risk_bearing_snapshots(snapshots, mode), product_id)
    held = () if incomplete is not None else _held_products(books)
    return (product_id, *(item for item in held if item != product_id))


def beta_verdict(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    proposed: ProposedEntry,
    occupied: Sequence[DeploymentSnapshot],
    live_quote_cash: Decimal | None,
    beta: BetaEvidence | None,
    as_of: datetime | None,
) -> RiskVerdict | None:
    """Deny when β-weighted exposure would exceed the cap or any needed β is unknown.

    ``occupied`` is the gate's same-quote risk-bearing book set. Returns ``None`` (no
    objection) when no β cap binds in ``mode``. Evidence that was never loaded, or an
    entry without an observation time to judge staleness, denies.
    """
    if not beta_cap_applies(policy, mode):
        return None
    reference = _reference_label(proposed.product_id)
    if beta is None or as_of is None:
        return _deny(
            RiskReasonCode.BTC_BETA_UNAVAILABLE,
            f"BTC beta unavailable for {proposed.product_id} vs {reference}: evidence was not "
            "loaded; new entries blocked.",
        )
    betas: dict[str, Decimal] = {}
    for product_id in (proposed.product_id, *_held_products(occupied)):
        result = fresh_beta(beta.result_for(product_id), as_of=as_of)
        if isinstance(result, BetaUnavailable):
            return _deny(
                RiskReasonCode.BTC_BETA_UNAVAILABLE,
                f"BTC beta unavailable for {product_id} vs {reference}: {result.describe()}; "
                "new entries blocked.",
            )
        betas[product_id] = max(result.beta, Decimal(0))
    existing = sum(
        (
            abs(product_exposure(book, product_id)) * betas[product_id]
            for book in occupied
            for product_id in _book_products(book)
            if product_id in betas
        ),
        Decimal(0),
    )
    proposed_beta = betas[proposed.product_id]
    weighted = abs(proposed.notional) * proposed_beta
    capital = _capital_base(
        policy,
        mode=mode,
        live_quote_cash=live_quote_cash,
        occupied=occupied,
        in_kind_notional=proposed.in_kind_capital,
    )
    cap = _beta_cap(policy, mode=mode, capital=capital)
    if capital > 0 and existing + weighted <= cap:
        return None
    return _deny(
        RiskReasonCode.BTC_BETA_EXPOSURE_EXCEEDED,
        f"BTC-beta exposure exceeded (reference {reference}): existing={_shown(existing)}, "
        f"proposed={_shown(proposed.notional)}*β{proposed_beta}={_shown(weighted)}, "
        f"cap={_shown(cap)}, capital={_shown(capital)}, "
        f"fraction={policy.max_btc_beta_exposure_fraction}, "
        f"absolute={policy.max_btc_beta_exposure_quote}.",
    )


def _beta_cap(policy: RiskPolicyDefinition, *, mode: DeploymentMode, capital: Decimal) -> Decimal:
    """Return the tighter of the fraction-derived and (live) absolute β caps.

    Only called when ``beta_cap_applies``, so at least one bound is set for ``mode``.
    """
    bounds: list[Decimal] = []
    if policy.max_btc_beta_exposure_fraction is not None:
        bounds.append(capital * Decimal(policy.max_btc_beta_exposure_fraction))
    if policy.max_btc_beta_exposure_quote is not None and mode is DeploymentMode.LIVE:
        bounds.append(Decimal(policy.max_btc_beta_exposure_quote))
    return min(bounds)


def _held_products(books: Sequence[DeploymentSnapshot]) -> tuple[str, ...]:
    """Sorted products with non-zero exposure in ``books``."""
    return tuple(
        sorted(
            {
                product_id
                for book in books
                for product_id in _book_products(book)
                if product_exposure(book, product_id) != 0
            }
        )
    )


def _reference_label(product_id: str) -> str:
    """Return ``BTC-<quote>`` for a spot product, or a placeholder for a malformed id."""
    return beta_reference(product_id) if is_spot_product_id(product_id) else "BTC-?"


def _shown(value: Decimal) -> str:
    """Render a derived quote amount at cent precision for a bounded denial detail."""
    return str(value.quantize(_SHOWN, rounding=ROUND_HALF_EVEN))
