"""Evaluate the latest closed candle's entry condition for a live/paper runtime."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.research.indicators import calculate_indicator_rows
from thytrader.research.signal_evaluator import entry_condition_outcome
from thytrader.research.trace import EntryConditionOutcome

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import StrategyDefinition


def evaluate_latest_entry(
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
) -> EntryConditionOutcome:
    """Evaluate entry conditions on the newest candle using prior-bar crossover state."""
    if len(candles) < 2:
        return EntryConditionOutcome.UNDEFINED
    rows = calculate_indicator_rows(strategy.indicators, candles)
    return entry_condition_outcome(strategy.entry.when, rows[-1], rows[-2])


def latest_atr(strategy: StrategyDefinition, candles: Sequence[Candle]) -> Decimal | None:
    """Return the newest ATR value used by the initial stop, or None when undefined."""
    indicator_id = strategy.exits.initial_stop.atr_indicator
    rows = calculate_indicator_rows(strategy.indicators, candles)
    if not rows:
        return None
    value = rows[-1].get(indicator_id)
    return value if isinstance(value, Decimal) else None
