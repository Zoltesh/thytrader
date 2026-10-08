"""Volatility, bands, and channels (ATR, Bollinger, Keltner, Donchian, and more)."""

from __future__ import annotations

from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING

from thytrader.research.indicator_math.core import (
    IndicatorCalculationError,
    _locked_source_series,
    _parameters_of,
    _period_parameter,
    _windows_defined,
)
from thytrader.research.indicator_math.moving_averages import (
    _exponential_moving_average,
    _simple_moving_average,
)
from thytrader.research.indicator_math.rolling_statistics import (
    _rolling_extreme,
    _rolling_population_stdev,
)
from thytrader.strategies.models import (
    BollingerIndicatorParameters,
    HistoricalVolatilityIndicatorParameters,
    KeltnerIndicatorParameters,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import IndicatorDefinition


def _bollinger_series(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> dict[str, tuple[Decimal | None, ...]]:
    """Return SMA middle and population-stdev bands of close."""
    parameters = indicator.parameters
    if not isinstance(parameters, BollingerIndicatorParameters):
        raise IndicatorCalculationError(
            f"Indicator kind {indicator.kind.value} is not implemented by this engine contract."
        )
    closes = _locked_source_series(indicator, candles)
    middle = _simple_moving_average(closes, parameters.period)
    deviation = _rolling_population_stdev(closes, parameters.period)
    multiplier = Decimal(parameters.stdev_multiplier)
    upper: list[Decimal | None] = []
    lower: list[Decimal | None] = []
    for mid, stdev in zip(middle, deviation, strict=True):
        if mid is None or stdev is None:
            upper.append(None)
            lower.append(None)
            continue
        width = multiplier * stdev
        upper.append(mid + width)
        lower.append(mid - width)
    return {"middle": middle, "upper": tuple(upper), "lower": tuple(lower)}


def _average_true_range(
    candles: Sequence[Candle],
    period: int,
) -> tuple[Decimal | None, ...]:
    """Return Wilder ATR seeded by the arithmetic mean of the first true ranges."""
    true_ranges: list[Decimal] = []
    result: list[Decimal | None] = []
    previous_close: Decimal | None = None
    previous_atr: Decimal | None = None
    for index, candle in enumerate(candles):
        high_low = candle.high - candle.low
        true_range = (
            high_low
            if previous_close is None
            else max(high_low, abs(candle.high - previous_close), abs(candle.low - previous_close))
        )
        true_ranges.append(true_range)
        previous_close = candle.close
        if index + 1 < period:
            result.append(None)
            continue
        if previous_atr is None:
            previous_atr = sum(true_ranges[-period:], start=Decimal(0)) / Decimal(period)
        else:
            previous_atr = (previous_atr * Decimal(period - 1) + true_range) / Decimal(period)
        result.append(previous_atr)
    return tuple(result)


def _true_range(candle: Candle, previous_close: Decimal | None) -> Decimal:
    """Return the shipped ATR true range: ``high - low`` on the first bar."""
    high_low = candle.high - candle.low
    if previous_close is None:
        return high_low
    return max(high_low, abs(candle.high - previous_close), abs(candle.low - previous_close))


def _true_ranges(candles: Sequence[Candle]) -> tuple[Decimal, ...]:
    """Return the shipped true range of every bar (first bar ``high - low``)."""
    ranges: list[Decimal] = []
    previous_close: Decimal | None = None
    for candle in candles:
        ranges.append(_true_range(candle, previous_close))
        previous_close = candle.close
    return tuple(ranges)


def _keltner_series(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> dict[str, tuple[Decimal | None, ...]]:
    """Return EMA(close) middle with bands ``multiplier * ATR(atr_period)`` away."""
    parameters = _parameters_of(indicator, KeltnerIndicatorParameters)
    middle = _exponential_moving_average(
        tuple(candle.close for candle in candles), parameters.period
    )
    atr = _average_true_range(candles, parameters.atr_period)
    multiplier = Decimal(parameters.multiplier)
    upper: list[Decimal | None] = []
    lower: list[Decimal | None] = []
    for mid, atr_value in zip(middle, atr, strict=True):
        if mid is None or atr_value is None:
            upper.append(None)
            lower.append(None)
            continue
        width = multiplier * atr_value
        upper.append(mid + width)
        lower.append(mid - width)
    return {"upper": tuple(upper), "middle": middle, "lower": tuple(lower)}


def _donchian_series(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> dict[str, tuple[Decimal | None, ...]]:
    """Return highest high, lowest low, and their midpoint over each inclusive window."""
    period = _period_parameter(indicator)
    upper = _rolling_extreme(tuple(candle.high for candle in candles), period, maximum=True)
    lower = _rolling_extreme(tuple(candle.low for candle in candles), period, maximum=False)
    two = Decimal(2)
    middle = tuple(
        None if high is None or low is None else (high + low) / two
        for high, low in zip(upper, lower, strict=True)
    )
    return {"upper": upper, "middle": middle, "lower": lower}


def _bollinger_percent_b_values(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> tuple[Decimal | None, ...]:
    """Return %B = (close - lower) / (upper - lower) on the shipped Bollinger bands.

    Equal bands (zero population stdev) are undefined.
    """
    bands = _bollinger_series(indicator, candles)
    result: list[Decimal | None] = []
    for candle, upper, lower in zip(candles, bands["upper"], bands["lower"], strict=True):
        if upper is None or lower is None or upper == lower:
            result.append(None)
            continue
        result.append((candle.close - lower) / (upper - lower))
    return tuple(result)


def _bollinger_bandwidth_values(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> tuple[Decimal | None, ...]:
    """Return bandwidth = (upper - lower) / middle on the shipped Bollinger bands."""
    bands = _bollinger_series(indicator, candles)
    result: list[Decimal | None] = []
    for middle, upper, lower in zip(bands["middle"], bands["upper"], bands["lower"], strict=True):
        if middle is None or upper is None or lower is None or middle == 0:
            result.append(None)
            continue
        result.append((upper - lower) / middle)
    return tuple(result)


def _normalized_average_true_range(
    candles: Sequence[Candle],
    period: int,
) -> tuple[Decimal | None, ...]:
    """Return NATR = 100 * ATR / close on the shipped Wilder ATR."""
    hundred = Decimal(100)
    return tuple(
        None if atr is None or candle.close == 0 else hundred * atr / candle.close
        for candle, atr in zip(candles, _average_true_range(candles, period), strict=True)
    )


def _choppiness_index(candles: Sequence[Candle], period: int) -> tuple[Decimal | None, ...]:
    """Return 100 * log10(sum TR / (highest high - lowest low)) / log10(period).

    True ranges follow ATR (first bar ``high - low``). ``Decimal.log10`` is correctly
    rounded in the 64-digit engine context. A zero high-low range is undefined.
    """
    ranges = _true_ranges(candles)
    highest = _rolling_extreme(tuple(candle.high for candle in candles), period, maximum=True)
    lowest = _rolling_extreme(tuple(candle.low for candle in candles), period, maximum=False)
    hundred = Decimal(100)
    log_period = Decimal(period).log10()
    result: list[Decimal | None] = []
    for index, (high, low) in enumerate(zip(highest, lowest, strict=True)):
        if high is None or low is None or high == low:
            result.append(None)
            continue
        range_sum = sum(ranges[index + 1 - period : index + 1], start=Decimal(0))
        result.append(hundred * (range_sum / (high - low)).log10() / log_period)
    return tuple(result)


def _historical_volatility_values(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> tuple[Decimal | None, ...]:
    """Return 100 * sample stdev of ``ln(close / prior close)`` over ``period`` returns.

    The ratio is rounded to the engine context, then ``Decimal.ln`` is correctly rounded.
    The stdev uses the shipped sample fold (divide by ``period - 1``). With
    ``annualization_periods`` the result is multiplied by its square root; without it the
    volatility is per bar. A non-positive close makes its returns undefined.
    """
    period = _period_parameter(indicator)
    parameters = indicator.parameters
    scale = (
        Decimal(parameters.annualization_periods).sqrt()
        if isinstance(parameters, HistoricalVolatilityIndicatorParameters)
        else None
    )
    returns: list[Decimal | None] = [None] if candles else []
    for previous, candle in pairwise(candles):
        if previous.close <= 0 or candle.close <= 0:
            returns.append(None)
            continue
        returns.append((candle.close / previous.close).ln())
    hundred = Decimal(100)
    result: list[Decimal | None] = []
    for window in _windows_defined(returns, period):
        if window is None:
            result.append(None)
            continue
        deviation = _sample_deviation(window)
        volatility = hundred * deviation
        result.append(volatility if scale is None else volatility * scale)
    return tuple(result)


def _sample_deviation(window: Sequence[Decimal]) -> Decimal:
    """Return the shipped sample stdev fold of one complete window (non-positive -> 0)."""
    mean = sum(window, start=Decimal(0)) / Decimal(len(window))
    sum_sq = Decimal(0)
    for value in window:
        delta = value - mean
        sum_sq += delta * delta
    variance = sum_sq / Decimal(len(window) - 1)
    return Decimal(0) if variance <= 0 else variance.sqrt()
