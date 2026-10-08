"""Rolling statistics: extremes, deviations, z-score, percent rank, regression."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.research.indicator_math.core import (
    _locked_source_series,
    _period_parameter,
    _windows_defined,
)
from thytrader.research.indicator_math.moving_averages import _simple_moving_average

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import IndicatorDefinition


def _rolling_extreme(
    values: Sequence[Decimal],
    period: int,
    *,
    maximum: bool,
) -> tuple[Decimal | None, ...]:
    """Return a rolling max or min after exactly one full inclusive window."""
    result: list[Decimal | None] = []
    for index in range(len(values)):
        if index + 1 < period:
            result.append(None)
            continue
        window = values[index + 1 - period : index + 1]
        extreme = window[0]
        for value in window[1:]:
            replace = value > extreme if maximum else value < extreme
            if replace:
                extreme = value
        result.append(extreme)
    return tuple(result)


def _rolling_population_stdev(
    values: Sequence[Decimal],
    period: int,
) -> tuple[Decimal | None, ...]:
    """Return population stdev of each inclusive window under engine Decimal rules."""
    return _rolling_stdev(values, period, divisor=period)


def _rolling_sample_stdev(
    values: Sequence[Decimal],
    period: int,
) -> tuple[Decimal | None, ...]:
    """Return sample stdev of each inclusive window under engine Decimal rules."""
    return _rolling_stdev(values, period, divisor=period - 1)


def _rolling_stdev(
    values: Sequence[Decimal],
    period: int,
    *,
    divisor: int,
) -> tuple[Decimal | None, ...]:
    """Return rolling stdev using population or sample divisor after the mean fold."""
    result: list[Decimal | None] = []
    period_decimal = Decimal(period)
    divisor_decimal = Decimal(divisor)
    for index in range(len(values)):
        if index + 1 < period:
            result.append(None)
            continue
        window = values[index + 1 - period : index + 1]
        mean = sum(window, start=Decimal(0)) / period_decimal
        sum_sq = Decimal(0)
        for value in window:
            delta = value - mean
            sum_sq += delta * delta
        variance = sum_sq / divisor_decimal
        result.append(Decimal(0) if variance <= 0 else variance.sqrt())
    return tuple(result)


def _rolling_highest(values: Sequence[Decimal], period: int) -> tuple[Decimal | None, ...]:
    """Return a rolling max after exactly one full inclusive window."""
    return _rolling_extreme(values, period, maximum=True)


def _rolling_lowest(values: Sequence[Decimal], period: int) -> tuple[Decimal | None, ...]:
    """Return a rolling min after exactly one full inclusive window."""
    return _rolling_extreme(values, period, maximum=False)


def _rolling_extreme_optional(
    values: Sequence[Decimal | None],
    period: int,
    *,
    maximum: bool,
) -> tuple[Decimal | None, ...]:
    """Return the shipped rolling max/min left-fold, undefined when the window has a hole."""
    result: list[Decimal | None] = []
    for window in _windows_defined(values, period):
        if window is None:
            result.append(None)
            continue
        extreme = window[0]
        for value in window[1:]:
            replace = value > extreme if maximum else value < extreme
            if replace:
                extreme = value
        result.append(extreme)
    return tuple(result)


def _linear_regression_series(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> dict[str, tuple[Decimal | None, ...]]:
    """Return the least-squares endpoint ``value`` and per-bar ``slope`` of each window.

    With x = 0 (oldest) .. period - 1 (current): ``slope = (n * Sxy - Sx * Sy) /
    (n * Sxx - Sx**2)``, ``intercept = (Sy - slope * Sx) / n``, ``value = intercept +
    slope * (n - 1)``. The x sums are exact integers; Sy and Sxy are oldest-first folds.
    """
    period = _period_parameter(indicator)
    values = _locked_source_series(indicator, candles)
    count = Decimal(period)
    sum_x_int = period * (period - 1) // 2
    sum_xx_int = (period - 1) * period * (2 * period - 1) // 6
    sum_x = Decimal(sum_x_int)
    divisor = Decimal(period * sum_xx_int - sum_x_int * sum_x_int)
    last_x = Decimal(period - 1)
    fitted: list[Decimal | None] = []
    slopes: list[Decimal | None] = []
    for index in range(len(values)):
        if index + 1 < period:
            fitted.append(None)
            slopes.append(None)
            continue
        sum_y = Decimal(0)
        sum_xy = Decimal(0)
        for position, value in enumerate(values[index + 1 - period : index + 1]):
            sum_y += value
            sum_xy += Decimal(position) * value
        slope = (count * sum_xy - sum_x * sum_y) / divisor
        intercept = (sum_y - slope * sum_x) / count
        fitted.append(intercept + slope * last_x)
        slopes.append(slope)
    return {"value": tuple(fitted), "slope": tuple(slopes)}


def _rolling_zscore(values: Sequence[Decimal], period: int) -> tuple[Decimal | None, ...]:
    """Return (value - SMA) / population stdev of the inclusive window; zero stdev undefined."""
    means = _simple_moving_average(values, period)
    deviations = _rolling_population_stdev(values, period)
    return tuple(
        None if mean is None or deviation is None or deviation == 0 else (value - mean) / deviation
        for value, mean, deviation in zip(values, means, deviations, strict=True)
    )


def _percent_rank(values: Sequence[Decimal], period: int) -> tuple[Decimal | None, ...]:
    """Return 100 * count(previous ``period`` values <= current) / period."""
    hundred = Decimal(100)
    divisor = Decimal(period)
    result: list[Decimal | None] = []
    for index, current in enumerate(values):
        if index < period:
            result.append(None)
            continue
        count = sum(1 for position in range(index - period, index) if values[position] <= current)
        result.append(hundred * Decimal(count) / divisor)
    return tuple(result)
