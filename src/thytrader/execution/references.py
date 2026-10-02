"""Paper/live readiness of read-only reference instruments (ADR 0096).

A strategy may read indicators from up to three reference series (for example a
BTC-USDC 1d regime gate on an alt). Paper and live load those bars every cycle. Before a
decision bar may open risk, every reference must have a contiguous closed-bar window
that ends with the reference bar that closed last at or before the decision close; the
window must also cover that reference's derived warmup. Otherwise new entries are
skipped for the bar with an explicit reason, and exits keep running.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from thytrader.execution.decisions import DecisionSkipReason
from thytrader.research.multi_timeframe import (
    bars_closed_at_or_before,
    closed_bar_starts,
    mapped_htf_start,
)
from thytrader.strategies.models import reference_data_requirements

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import ReferenceDataRequirement, StrategyDefinition


@dataclass(frozen=True, slots=True)
class ReferenceGate:
    """Why one decision bar may not open risk: a reference series is stale or missing.

    ``reason`` is ``REFERENCE_DATA_STALE`` (the newest closed reference bar is older than
    the bar the decision close needs) or ``REFERENCE_DATA_MISSING`` (no bars, a gap, or
    not enough history for the reference warmup). ``detail`` names the series.
    """

    reason: DecisionSkipReason
    detail: str


def reference_gate(
    strategy: StrategyDefinition,
    reference_candles: Mapping[str, Sequence[Candle]],
    *,
    decision_close: datetime,
) -> ReferenceGate | None:
    """Return the first unready reference for one decision close, or None when all are ready.

    Only bars that had closed by ``decision_close`` count, so an in-progress reference
    bar can never satisfy the gate (no lookahead). References are checked in
    declaration order.
    """
    for requirement in reference_data_requirements(strategy):
        gate = _requirement_gate(
            requirement,
            reference_candles.get(requirement.reference_id) or (),
            decision_close=decision_close,
            decision_timeframe=strategy.timeframe,
        )
        if gate is not None:
            return gate
    return None


def _requirement_gate(
    requirement: ReferenceDataRequirement,
    candles: Sequence[Candle],
    *,
    decision_close: datetime,
    decision_timeframe: str,
) -> ReferenceGate | None:
    """Classify one reference window against the bars this decision close reads."""
    label = (
        f"reference {requirement.reference_id} ({requirement.product_id} {requirement.timeframe})"
    )
    visible = bars_closed_at_or_before(
        candles, close_at=decision_close, timeframe=requirement.timeframe
    )
    if not visible:
        return ReferenceGate(
            reason=DecisionSkipReason.REFERENCE_DATA_MISSING,
            detail=f"{label}: no closed bars were returned by the market-data provider",
        )
    expected_last = mapped_htf_start(decision_close, requirement.timeframe)
    newest = visible[-1].starts_at
    if newest < expected_last:
        return ReferenceGate(
            reason=DecisionSkipReason.REFERENCE_DATA_STALE,
            detail=(
                f"{label}: newest closed bar opens {_utc(newest)}, but this decision bar "
                f"needs the bar opening {_utc(expected_last)}"
            ),
        )
    decision_starts_at = _decision_start(decision_close, decision_timeframe)
    required = closed_bar_starts(
        evaluation_starts_at=decision_starts_at,
        evaluation_ends_at=decision_close,
        timeframe=requirement.timeframe,
        warmup_bars=requirement.warmup_bars,
    )
    present = {candle.starts_at for candle in visible}
    missing = [start for start in required if start not in present]
    if missing:
        return ReferenceGate(
            reason=DecisionSkipReason.REFERENCE_DATA_MISSING,
            detail=(
                f"{label}: {len(missing)} of {len(required)} closed bars needed for its "
                f"{requirement.warmup_bars}-bar warmup are missing (first {_utc(missing[0])})"
            ),
        )
    return None


def _decision_start(decision_close: datetime, decision_timeframe: str) -> datetime:
    """Return the open of the decision bar that closes at ``decision_close``."""
    return mapped_htf_start(decision_close, decision_timeframe)


def _utc(instant: datetime) -> str:
    """Render one UTC bar boundary compactly for operator-facing detail."""
    return instant.isoformat().replace("+00:00", "Z")
