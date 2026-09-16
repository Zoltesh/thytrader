"""Evaluate the latest closed candle's entry condition for a live/paper runtime."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.research.indicators import calculate_indicator_rows
from thytrader.research.multi_timeframe import bars_closed_at_or_before, ltf_close
from thytrader.research.signal_evaluator import (
    SignalEvaluationError,
    and_entry_outcomes,
    calculate_extra_indicator_rows,
    calculate_htf_indicator_rows,
    entry_condition_outcome,
    htf_filter_outcome,
    overlay_indicator_timeframe_values,
)
from thytrader.research.trace import EntryConditionOutcome
from thytrader.strategies.models import decision_clock_indicators

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import StrategyDefinition


def evaluate_latest_entry(
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    htf_candles: Sequence[Candle] = (),
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None = None,
) -> EntryConditionOutcome:
    """Evaluate LTF entry AND optional HTF filter on the newest closed LTF bar.

    Extra-TF LTF-list indicators and HTF-filter values come from last-completed
    bars only. In-progress bars are dropped. Missing required coverage fails closed.
    """
    extra_candles = dict(indicator_timeframe_candles or {})
    htf_filter = strategy.htf_filter
    if htf_filter is None and htf_candles:
        raise SignalEvaluationError("HTF candles were supplied without an HTF filter.")
    if htf_filter is not None and not htf_candles:
        raise SignalEvaluationError("HTF candles are required for an HTF-filter strategy.")
    if len(candles) < 2:
        return EntryConditionOutcome.UNDEFINED
    latest = candles[-1]
    current_close = ltf_close(latest.starts_at, strategy.timeframe)
    visible_htf = htf_candles
    if htf_filter is not None:
        visible_htf = bars_closed_at_or_before(
            htf_candles,
            close_at=current_close,
            timeframe=htf_filter.timeframe,
        )
    visible_extra = {
        timeframe: bars_closed_at_or_before(bars, close_at=current_close, timeframe=timeframe)
        for timeframe, bars in extra_candles.items()
    }
    extra_rows = calculate_extra_indicator_rows(
        strategy,
        visible_extra,
        visible_htf,
        evaluation_starts_at=latest.starts_at,
        evaluation_ends_at=current_close,
    )
    rows = calculate_indicator_rows(decision_clock_indicators(strategy), candles)
    merged_values, merged_previous = overlay_indicator_timeframe_values(
        strategy,
        latest,
        previous_ltf_start=candles[-2].starts_at,
        ltf_values=rows[-1],
        previous_ltf_values=rows[-2],
        extra_rows=extra_rows,
    )
    ltf_outcome = entry_condition_outcome(strategy.entry.when, merged_values, merged_previous)
    if htf_filter is None:
        return ltf_outcome
    htf_rows = calculate_htf_indicator_rows(
        strategy,
        visible_htf,
        evaluation_starts_at=latest.starts_at,
        evaluation_ends_at=current_close,
    )
    htf_outcome, _htf_values = htf_filter_outcome(
        strategy,
        latest,
        previous_ltf_start=candles[-2].starts_at,
        htf_rows=htf_rows,
    )
    return and_entry_outcomes(ltf_outcome, htf_outcome)


def latest_atr(strategy: StrategyDefinition, candles: Sequence[Candle]) -> Decimal | None:
    """Return the newest ATR value used by the initial stop, or None when undefined."""
    return named_atr(strategy, candles, strategy.exits.initial_stop.atr_indicator)


def named_atr(
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    indicator_id: str,
) -> Decimal | None:
    """Return the newest value of one named ATR, or None when undefined."""
    rows = calculate_indicator_rows(decision_clock_indicators(strategy), candles)
    if not rows:
        return None
    value = rows[-1].get(indicator_id)
    return value if isinstance(value, Decimal) else None
