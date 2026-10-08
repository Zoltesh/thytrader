"""Bar grid of the backtest kernel: the run's bar interval and contiguous candle coverage.

Both checks fail closed with ``BacktestSimulationError`` before any simulation.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from thytrader.backtest.kernel_state import BacktestSimulationError
from thytrader.evaluation.models import ResearchRunSpecification, specification_bar_interval
from thytrader.market_data.models import CandleInterval, parse_candle_interval
from thytrader.market_data.quality import (
    CandleQualityError,
    validate_candle_timestamp,
    validate_candle_values,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import StrategyDefinition


def _candle_map(
    specification: ResearchRunSpecification,
    candles: Sequence[Candle],
    interval: CandleInterval,
) -> Mapping[datetime, Candle]:
    """Require exactly one well-formed candle from warmup through the terminal boundary bar."""
    starts_at = specification.warmup.starts_at
    bar = interval.duration
    try:
        ends_at = specification.evaluation.ends_at + bar
        for candle in candles:
            validate_candle_timestamp(candle.starts_at)
        selected = tuple(candle for candle in candles if starts_at <= candle.starts_at < ends_at)
        for candle in selected:
            validate_candle_values(candle)
        expected_count = int((ends_at - starts_at) / bar)
        mapped = {candle.starts_at: candle for candle in selected}
        expected_starts = tuple(starts_at + bar * offset for offset in range(expected_count))
    except CandleQualityError as error:
        message = "Backtest candles have invalid timestamps or values."
        raise BacktestSimulationError(message) from error
    except OverflowError as error:
        message = "Backtest candle coverage exceeds the representable timestamp range."
        raise BacktestSimulationError(message) from error
    except (AttributeError, TypeError, ValueError) as error:
        message = "Backtest candles have invalid timestamps or values."
        raise BacktestSimulationError(message) from error
    if len(selected) != expected_count:
        raise BacktestSimulationError("Backtest candle coverage is incomplete or duplicated.")
    if len(mapped) != expected_count:
        raise BacktestSimulationError("Backtest candle coverage is duplicated.")
    for expected_start in expected_starts:
        candle = mapped.get(expected_start)
        if candle is None or candle.open <= 0 or candle.high < candle.low:
            raise BacktestSimulationError("Backtest candles are not valid contiguous OHLC bars.")
    return mapped


def _bar_interval(
    specification: ResearchRunSpecification,
    strategy: StrategyDefinition,
) -> CandleInterval:
    """Require the run windows and published strategy to share one supported bar duration."""
    strategy_interval = parse_candle_interval(strategy.timeframe)
    spec_interval = specification_bar_interval(specification)
    if strategy_interval is not spec_interval:
        raise BacktestSimulationError(
            "Backtest timeframe does not match the strategy and run windows."
        )
    span = specification.evaluation.ends_at - specification.evaluation.starts_at
    if span < strategy_interval.duration or span % strategy_interval.duration != timedelta(0):
        raise BacktestSimulationError(
            "Backtest evaluation window is not a positive multiple of the strategy timeframe."
        )
    return strategy_interval
