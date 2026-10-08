"""Bounded per-bar explanations for published backtest results (ADR 0116).

An operator can read what one backtest decided on each evaluation bar — the entry and
signal-exit outcomes with the exact indicator values the rule read, plus the simulated
fills and equity mark the immutable result records for that bar — one bounded page at
a time, with the result/run/strategy/dataset provenance that makes the page
reproducible.

Nothing is re-simulated and no new trace is stored: the page reuses the verified
re-evaluation of the run's entry-condition trace (the same fingerprint-checked path as
``GET /api/v1/backtests/{id}/signal-trace``) and joins it to the immutable result's
own trades and equity curve. Indicator values therefore come from completed bars only,
with no lookahead, and execution facts come only from the published result.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from thytrader.evaluation.models import BacktestEngine, FingerprintText
from thytrader.evaluation.trace import EntryConditionOutcome, IndicatorId

if TYPE_CHECKING:
    from collections.abc import Callable

    from thytrader.backtest.models import BacktestEvaluationWindow, BacktestResult
    from thytrader.evaluation.trace import SignalTrace

BAR_EXPLANATION_PAGE_MAX_LIMIT = 500
BAR_EXPLANATION_PAGE_DEFAULT_LIMIT = 100
BAR_EXPLANATION_SCHEMA_VERSION: Literal["thytrader-backtest-bar-explanation-v1"] = (
    "thytrader-backtest-bar-explanation-v1"
)


class _FrozenExplanationModel(BaseModel):
    """Reject unknown fields and prevent mutation of explanation evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _utc_text(value: datetime) -> str:
    """Render one UTC instant with the canonical Z suffix."""
    return value.isoformat().replace("+00:00", "Z")


class BarExplanationFill(_FrozenExplanationModel):
    """One simulated fill the immutable result records for this bar."""

    price: str
    quantity: str
    notional: str
    fee: str
    fee_rate: str


class BarExplanationEntry(BarExplanationFill):
    """One simulated entry (or pyramiding add) filled on this bar."""


class BarExplanationExit(BarExplanationFill):
    """One simulated exit filled on this bar, with its deterministic reason."""

    reason: Literal["stop_loss", "take_profit", "time_exit", "signal", "evaluation_end"]
    gross_pnl: str
    net_pnl: str
    holding_bars: int = Field(ge=0)


class BarExplanationOutsideTrace(_FrozenExplanationModel):
    """A result fill whose candle is not an evaluated signal bar.

    The evaluation-end liquidation uses the open of the bar at ``evaluation.ends_at``.
    That bar is never passed to indicator or condition evaluation, so it cannot appear
    inside the trace page. Listing it here keeps the close visible without implying the
    strategy saw that bar.
    """

    candle_starts_at: datetime
    kind: Literal["entry", "exit"]
    fill: BarExplanationEntry | BarExplanationExit

    @field_serializer("candle_starts_at", when_used="json")
    def serialize_outside_candle(self, value: datetime) -> str:
        """Render the outside-trace candle canonically."""
        return _utc_text(value)


class BarExplanationEquity(_FrozenExplanationModel):
    """The result's mark-to-market account value at this bar's close."""

    cash: str
    base_quantity: str
    mark_price: str
    equity: str


class BarExplanationIndicatorValue(_FrozenExplanationModel):
    """One declared indicator output key and the exact value the rule read."""

    indicator_id: IndicatorId
    value: str | None


class BacktestBarExplanation(_FrozenExplanationModel):
    """What one evaluation bar decided, and what the simulation did on it.

    ``exit_condition`` is null when the strategy declares no signal-exit rule.
    ``entries``/``exits``/``equity`` are empty or null exactly when the immutable
    result records nothing for this bar; nothing is interpolated.
    """

    candle_starts_at: datetime
    entry_condition: EntryConditionOutcome
    exit_condition: EntryConditionOutcome | None = None
    indicator_values: tuple[BarExplanationIndicatorValue, ...] = Field(min_length=1)
    entries: tuple[BarExplanationEntry, ...] = ()
    exits: tuple[BarExplanationExit, ...] = ()
    equity: BarExplanationEquity | None = None

    @field_serializer("candle_starts_at", when_used="json")
    def serialize_candle(self, value: datetime) -> str:
        """Render the bar boundary canonically."""
        return _utc_text(value)


class BacktestBarExplanationPage(_FrozenExplanationModel):
    """One bounded page of per-bar explanations with full source provenance."""

    schema_version: Literal["thytrader-backtest-bar-explanation-v1"] = (
        BAR_EXPLANATION_SCHEMA_VERSION
    )
    result_fingerprint: FingerprintText
    run_fingerprint: FingerprintText
    strategy_fingerprint: FingerprintText
    dataset_fingerprint: FingerprintText
    signal_trace_fingerprint: FingerprintText
    engine: BacktestEngine
    product_id: str
    timeframe: str
    evaluation_start: datetime
    evaluation_end: datetime
    total_bars: int = Field(ge=0)
    limit: int = Field(ge=1, le=BAR_EXPLANATION_PAGE_MAX_LIMIT)
    offset: int = Field(ge=0)
    returned: int = Field(ge=0)
    records: tuple[BacktestBarExplanation, ...] = ()
    outside_trace: tuple[BarExplanationOutsideTrace, ...] = ()
    next_cursor: str | None = None

    @field_serializer("evaluation_start", "evaluation_end", when_used="json")
    def serialize_window(self, value: datetime) -> str:
        """Render the evaluation window canonically."""
        return _utc_text(value)


def bar_explanation_page(
    trace: SignalTrace,
    result: BacktestResult,
    *,
    window: BacktestEvaluationWindow,
    product_id: str,
    limit: int,
    offset: int,
    result_fingerprint: str,
    next_cursor_for: Callable[[int], str],
) -> BacktestBarExplanationPage:
    """Join one verified trace to the immutable result's per-bar execution facts.

    Args:
        trace: The fingerprint-verified re-evaluation of the result's run.
        result: The immutable published result supplying trades and equity marks.
        window: The result's derived evaluation window (timeframe and bounds).
        product_id: The primary product the trace describes.
        limit: Page size (1..``BAR_EXPLANATION_PAGE_MAX_LIMIT``).
        offset: Zero-based bar offset into the evaluation window.
        result_fingerprint: The result's canonical identity.
        next_cursor_for: Builds the opaque cursor for the next offset.

    Returns:
        One bounded page of per-bar explanations, oldest bar first.
    """
    entries_by_bar = _index_entries(result)
    exits_by_bar = _index_exits(result)
    equity_by_bar = {point.candle_starts_at: point for point in result.equity_curve}
    records: list[BacktestBarExplanation] = []
    for record in trace.records[offset : offset + limit]:
        bar = record.candle_starts_at
        records.append(
            BacktestBarExplanation(
                candle_starts_at=bar,
                entry_condition=record.entry_condition,
                exit_condition=record.exit_condition,
                indicator_values=tuple(
                    BarExplanationIndicatorValue(indicator_id=item.indicator_id, value=item.value)
                    for item in record.indicator_values
                ),
                entries=entries_by_bar.get(bar, ()),
                exits=exits_by_bar.get(bar, ()),
                equity=(
                    None
                    if (point := equity_by_bar.get(bar)) is None
                    else BarExplanationEquity(
                        cash=point.cash,
                        base_quantity=point.base_quantity,
                        mark_price=point.mark_price,
                        equity=point.equity,
                    )
                ),
            )
        )
    has_more = offset + limit < len(trace.records)
    traced_bars = {record.candle_starts_at for record in trace.records}
    return BacktestBarExplanationPage(
        result_fingerprint=result_fingerprint,
        run_fingerprint=result.run_fingerprint,
        strategy_fingerprint=result.strategy_fingerprint,
        dataset_fingerprint=result.dataset_fingerprint,
        signal_trace_fingerprint=result.signal_trace_fingerprint,
        engine=result.engine,
        product_id=product_id,
        timeframe=window.timeframe,
        evaluation_start=window.evaluation_start,
        evaluation_end=window.evaluation_end,
        total_bars=len(trace.records),
        limit=limit,
        offset=offset,
        returned=len(records),
        records=tuple(records),
        outside_trace=_outside_trace(result, traced_bars) if offset == 0 else (),
        next_cursor=next_cursor_for(offset + limit) if has_more else None,
    )


def _outside_trace(
    result: BacktestResult, traced_bars: set[datetime]
) -> tuple[BarExplanationOutsideTrace, ...]:
    """List result fills whose candles were not evaluated as signals."""
    outside: list[BarExplanationOutsideTrace] = []
    for trade in result.trades:
        entry = trade.entry
        if entry.candle_starts_at not in traced_bars:
            outside.append(
                BarExplanationOutsideTrace(
                    candle_starts_at=entry.candle_starts_at,
                    kind="entry",
                    fill=BarExplanationEntry(
                        price=entry.price,
                        quantity=entry.quantity,
                        notional=entry.notional,
                        fee=entry.fee,
                        fee_rate=entry.fee_rate,
                    ),
                )
            )
        exit_fill = trade.exit
        if exit_fill.candle_starts_at not in traced_bars:
            outside.append(
                BarExplanationOutsideTrace(
                    candle_starts_at=exit_fill.candle_starts_at,
                    kind="exit",
                    fill=BarExplanationExit(
                        price=exit_fill.price,
                        quantity=exit_fill.quantity,
                        notional=exit_fill.notional,
                        fee=exit_fill.fee,
                        fee_rate=exit_fill.fee_rate,
                        reason=exit_fill.reason,
                        gross_pnl=trade.gross_pnl,
                        net_pnl=trade.net_pnl,
                        holding_bars=trade.holding_bars,
                    ),
                )
            )
    return tuple(outside)


def _index_entries(
    result: BacktestResult,
) -> dict[datetime, tuple[BarExplanationEntry, ...]]:
    """Index the result's simulated entry fills by their entry bar."""
    indexed: dict[datetime, list[BarExplanationEntry]] = {}
    for trade in result.trades:
        fill = trade.entry
        indexed.setdefault(fill.candle_starts_at, []).append(
            BarExplanationEntry(
                price=fill.price,
                quantity=fill.quantity,
                notional=fill.notional,
                fee=fill.fee,
                fee_rate=fill.fee_rate,
            )
        )
    return {bar: tuple(fills) for bar, fills in indexed.items()}


def _index_exits(result: BacktestResult) -> dict[datetime, tuple[BarExplanationExit, ...]]:
    """Index the result's simulated exit fills by their exit bar."""
    indexed: dict[datetime, list[BarExplanationExit]] = {}
    for trade in result.trades:
        fill = trade.exit
        indexed.setdefault(fill.candle_starts_at, []).append(
            BarExplanationExit(
                price=fill.price,
                quantity=fill.quantity,
                notional=fill.notional,
                fee=fill.fee,
                fee_rate=fill.fee_rate,
                reason=fill.reason,
                gross_pnl=trade.gross_pnl,
                net_pnl=trade.net_pnl,
                holding_bars=trade.holding_bars,
            )
        )
    return {bar: tuple(fills) for bar, fills in indexed.items()}


__all__ = [
    "BAR_EXPLANATION_PAGE_DEFAULT_LIMIT",
    "BAR_EXPLANATION_PAGE_MAX_LIMIT",
    "BAR_EXPLANATION_SCHEMA_VERSION",
    "BacktestBarExplanation",
    "BacktestBarExplanationPage",
    "BarExplanationEntry",
    "BarExplanationEquity",
    "BarExplanationExit",
    "BarExplanationFill",
    "BarExplanationIndicatorValue",
    "BarExplanationOutsideTrace",
    "bar_explanation_page",
]
