"""Decision, HTF, and per-indicator timeframe helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from thytrader.market_data.models import (
    EXECUTION_TIMEFRAMES,
    DatasetTimeframe,
    parse_candle_interval,
)
from thytrader.strategies.schema.indicator_definition import _indicator_input_fields
from thytrader.strategies.schema.indicator_warmup import _indicator_min_warmup

if TYPE_CHECKING:
    from thytrader.strategies.schema.conditions import IndicatorOperand
    from thytrader.strategies.schema.indicator_definition import IndicatorDefinition


STRATEGY_DECISION_TIMEFRAMES: tuple[DatasetTimeframe, ...] = EXECUTION_TIMEFRAMES
STRATEGY_HTF_TIMEFRAMES: tuple[DatasetTimeframe, ...] = EXECUTION_TIMEFRAMES


def timeframe_seconds(timeframe: str) -> int:
    """Return the exact duration of one supported strategy timeframe in seconds."""
    try:
        return int(parse_candle_interval(timeframe).duration.total_seconds())
    except ValueError as error:
        raise ValueError(f"unsupported strategy timeframe: {timeframe}") from error


def is_valid_reference_pair(decision_timeframe: str, reference_timeframe: str) -> bool:
    """Return whether a reference clock is the decision clock or a coarser integer multiple."""
    if reference_timeframe == decision_timeframe:
        return True
    return is_valid_htf_pair(decision_timeframe, reference_timeframe)


def is_valid_htf_pair(decision_timeframe: str, htf_timeframe: str) -> bool:
    """Return whether HTF is strictly coarser and an integer multiple of the LTF clock."""
    try:
        decision_seconds = timeframe_seconds(decision_timeframe)
        htf_seconds = timeframe_seconds(htf_timeframe)
    except ValueError:
        return False
    if htf_seconds <= decision_seconds:
        return False
    return htf_seconds % decision_seconds == 0


def resolved_indicator_timeframe(indicator: IndicatorDefinition, decision_timeframe: str) -> str:
    """Return the clock one indicator evaluates on, defaulting to the decision timeframe."""
    if indicator.timeframe is None:
        return decision_timeframe
    return indicator.timeframe


def extra_indicator_timeframe_warmup(
    indicators: tuple[IndicatorDefinition, ...],
    *,
    operands: tuple[IndicatorOperand, ...] = (),
) -> int:
    """Return native-clock warmup including the largest operand lag for each indicator."""
    return max(
        (
            _indicator_min_warmup(indicator)
            + max(
                (operand.offset or 0 for operand in operands if operand.indicator == indicator.id),
                default=0,
            )
            for indicator in indicators
        ),
        default=1,
    )


def extra_indicator_required_fields(
    indicators: tuple[IndicatorDefinition, ...],
) -> tuple[Literal["open", "high", "low", "close", "volume"], ...]:
    """Return unique OHLCV fields consumed by one extra-TF indicator group, catalog order."""
    needed = {field for indicator in indicators for field in _indicator_input_fields(indicator)}
    catalog: tuple[Literal["open", "high", "low", "close", "volume"], ...] = (
        "open",
        "high",
        "low",
        "close",
        "volume",
    )
    return tuple(field for field in catalog if field in needed)
