"""Evaluate the latest closed candle's entry condition for a live/paper runtime."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.research.indicators import calculate_indicator_rows
from thytrader.research.multi_timeframe import htf_bars_closed_at_or_before, ltf_close
from thytrader.research.signal_evaluator import (
    SignalEvaluationError,
    and_entry_outcomes,
    calculate_htf_indicator_rows,
    entry_condition_outcome,
    htf_filter_outcome,
)
from thytrader.research.trace import EntryConditionOutcome

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import StrategyDefinition


def evaluate_latest_entry(
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    htf_candles: Sequence[Candle] = (),
) -> EntryConditionOutcome:
    """Evaluate LTF entry AND optional HTF filter on the newest closed LTF bar.

    HTF values come from last-completed HTF bars only. In-progress HTF bars are
    dropped. Missing required HTF coverage fails closed rather than ignoring the
    published filter.
    """
    htf_filter = strategy.htf_filter
    if htf_filter is None:
        if htf_candles:
            raise SignalEvaluationError("HTF candles were supplied without an HTF filter.")
        if len(candles) < 2:
            return EntryConditionOutcome.UNDEFINED
        rows = calculate_indicator_rows(strategy.indicators, candles)
        return entry_condition_outcome(strategy.entry.when, rows[-1], rows[-2])
    if not htf_candles:
        raise SignalEvaluationError("HTF candles are required for an HTF-filter strategy.")
    if len(candles) < 2:
        return EntryConditionOutcome.UNDEFINED
    latest = candles[-1]
    current_close = ltf_close(latest.starts_at, strategy.timeframe)
    visible = htf_bars_closed_at_or_before(
        htf_candles,
        close_at=current_close,
        htf_timeframe=htf_filter.timeframe,
    )
    htf_rows = calculate_htf_indicator_rows(
        strategy,
        visible,
        evaluation_starts_at=latest.starts_at,
        evaluation_ends_at=current_close,
    )
    rows = calculate_indicator_rows(strategy.indicators, candles)
    ltf_outcome = entry_condition_outcome(strategy.entry.when, rows[-1], rows[-2])
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
    rows = calculate_indicator_rows(strategy.indicators, candles)
    if not rows:
        return None
    value = rows[-1].get(indicator_id)
    return value if isinstance(value, Decimal) else None
