"""Minimum whole-contract admission probes shared by read-only futures consumers."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import DecimalException, Inexact, localcontext
from typing import TYPE_CHECKING

from thytrader.risk.futures_live import live_futures_entry_verdict, live_futures_opt_in_verdict
from thytrader.risk.gate_common import ProposedEntry, _deny
from thytrader.risk.models import RiskReasonCode

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from decimal import Decimal

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

    This is an advisory one-contract probe, not a strategy signal or a guarantee of a
    future order. Missing evidence denies; protective exits never consume this helper.
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
    block = probe.state.entry_block()
    if block is not None:
        return (_deny(RiskReasonCode(block[0]), block[1]),)
    if probe.price is None or not probe.price.is_finite() or probe.price <= 0:
        return (
            _deny(
                RiskReasonCode.FUTURES_COLLATERAL_UNKNOWN,
                "A fresh positive mark is required for a minimum one-contract admission probe.",
            ),
        )
    margin = probe.state.margin
    if margin is None:
        return (_deny(RiskReasonCode.FUTURES_MARGIN_UNKNOWN, "Margin terms are unknown."),)
    try:
        with localcontext() as context:
            context.traps[Inexact] = True
            notional = margin.contract_size * probe.price
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
