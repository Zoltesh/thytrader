"""Moving averages and smoothing (SMA, EMA, WMA, DEMA, TEMA, HMA, KAMA, Wilder)."""

from __future__ import annotations

from decimal import Decimal
from math import isqrt
from typing import TYPE_CHECKING

from thytrader.research.indicator_math.core import (
    _locked_source_series,
    _parameters_of,
    _windows_defined,
)
from thytrader.strategies.models import KamaIndicatorParameters

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import IndicatorDefinition


def _simple_moving_average_defined(
    values: Sequence[Decimal | None],
    period: int,
) -> tuple[Decimal | None, ...]:
    """Return SMA of an aligned series, undefined when any window value is missing."""
    result: list[Decimal | None] = []
    period_decimal = Decimal(period)
    for index in range(len(values)):
        if index + 1 < period:
            result.append(None)
            continue
        window = values[index + 1 - period : index + 1]
        if any(value is None for value in window):
            result.append(None)
            continue
        total = sum((value for value in window if value is not None), start=Decimal(0))
        result.append(total / period_decimal)
    return tuple(result)


def _wilder_smooth(values: Sequence[Decimal], period: int) -> tuple[Decimal | None, ...]:
    """Return ATR-style Wilder smoothing of a fully defined series."""
    result: list[Decimal | None] = []
    previous: Decimal | None = None
    for index, value in enumerate(values):
        if index + 1 < period:
            result.append(None)
            continue
        if previous is None:
            previous = sum(values[:period], start=Decimal(0)) / Decimal(period)
        else:
            previous = (previous * Decimal(period - 1) + value) / Decimal(period)
        result.append(previous)
    return tuple(result)


def _wilder_smooth_optional(
    values: Sequence[Decimal | None],
    period: int,
) -> tuple[Decimal | None, ...]:
    """Return Wilder smoothing that seeds from defined values and skips holes."""
    result: list[Decimal | None] = []
    seed: list[Decimal] = []
    previous: Decimal | None = None
    period_decimal = Decimal(period)
    for value in values:
        if value is None:
            result.append(None)
            continue
        if previous is None:
            seed.append(value)
            if len(seed) < period:
                result.append(None)
                continue
            previous = sum(seed, start=Decimal(0)) / period_decimal
            result.append(previous)
            continue
        previous = (previous * Decimal(period - 1) + value) / period_decimal
        result.append(previous)
    return tuple(result)


def _simple_moving_average(
    values: Sequence[Decimal],
    period: int,
) -> tuple[Decimal | None, ...]:
    """Return a rolling arithmetic mean after exactly one full period is available."""
    result: list[Decimal | None] = []
    for index in range(len(values)):
        if index + 1 < period:
            result.append(None)
            continue
        window = values[index + 1 - period : index + 1]
        result.append(sum(window, start=Decimal(0)) / Decimal(period))
    return tuple(result)


def _exponential_moving_average(
    values: Sequence[Decimal],
    period: int,
) -> tuple[Decimal | None, ...]:
    """Return SMA-seeded EMA values using the contract's ordered recurrence."""
    result: list[Decimal | None] = []
    previous: Decimal | None = None
    for index, value in enumerate(values):
        if index + 1 < period:
            result.append(None)
            continue
        if previous is None:
            previous = sum(values[:period], start=Decimal(0)) / Decimal(period)
        else:
            previous = (Decimal(period - 1) * previous + Decimal(2) * value) / Decimal(period + 1)
        result.append(previous)
    return tuple(result)


def _weighted_moving_average(
    values: Sequence[Decimal],
    period: int,
) -> tuple[Decimal | None, ...]:
    """Return a rolling WMA with oldest weight 1 and newest weight ``period``."""
    result: list[Decimal | None] = []
    for index in range(len(values)):
        if index + 1 < period:
            result.append(None)
            continue
        window = values[index + 1 - period : index + 1]
        weighted = Decimal(0)
        weight_sum = Decimal(0)
        for offset, value in enumerate(window, start=1):
            weight = Decimal(offset)
            weighted += value * weight
            weight_sum += weight
        result.append(weighted / weight_sum)
    return tuple(result)


def _exponential_moving_average_optional(
    values: Sequence[Decimal | None],
    period: int,
) -> tuple[Decimal | None, ...]:
    """Return the shipped SMA-seeded EMA over a series that may contain undefined values.

    The seed is the left-fold mean of the first ``period`` defined values. An undefined
    value yields undefined output and does not advance the recurrence (the ADX
    convention), so a fully defined suffix produces exactly the shipped EMA.
    """
    result: list[Decimal | None] = []
    seed: list[Decimal] = []
    previous: Decimal | None = None
    for value in values:
        if value is None:
            result.append(None)
            continue
        if previous is None:
            seed.append(value)
            if len(seed) < period:
                result.append(None)
                continue
            previous = sum(seed, start=Decimal(0)) / Decimal(period)
            result.append(previous)
            continue
        previous = (Decimal(period - 1) * previous + Decimal(2) * value) / Decimal(period + 1)
        result.append(previous)
    return tuple(result)


def _weighted_moving_average_optional(
    values: Sequence[Decimal | None],
    period: int,
) -> tuple[Decimal | None, ...]:
    """Return the shipped WMA left-fold, undefined when the window has a hole."""
    result: list[Decimal | None] = []
    for window in _windows_defined(values, period):
        if window is None:
            result.append(None)
            continue
        weighted = Decimal(0)
        weight_sum = Decimal(0)
        for offset, value in enumerate(window, start=1):
            weight = Decimal(offset)
            weighted += value * weight
            weight_sum += weight
        result.append(weighted / weight_sum)
    return tuple(result)


def _double_exponential_moving_average(
    values: Sequence[Decimal],
    period: int,
) -> tuple[Decimal | None, ...]:
    """Return DEMA = 2 * EMA - EMA(EMA) with both EMAs on the shipped SMA-seeded recurrence."""
    first = _exponential_moving_average(values, period)
    second = _exponential_moving_average_optional(first, period)
    two = Decimal(2)
    return tuple(
        None if outer is None or inner is None else two * outer - inner
        for outer, inner in zip(first, second, strict=True)
    )


def _triple_exponential_moving_average(
    values: Sequence[Decimal],
    period: int,
) -> tuple[Decimal | None, ...]:
    """Return TEMA = (3 * EMA - 3 * EMA(EMA)) + EMA(EMA(EMA)), evaluated left to right."""
    first = _exponential_moving_average(values, period)
    second = _exponential_moving_average_optional(first, period)
    third = _exponential_moving_average_optional(second, period)
    three = Decimal(3)
    return tuple(
        None if one is None or two is None or tri is None else three * one - three * two + tri
        for one, two, tri in zip(first, second, third, strict=True)
    )


def _hull_moving_average(values: Sequence[Decimal], period: int) -> tuple[Decimal | None, ...]:
    """Return WMA(2 * WMA(period // 2) - WMA(period), isqrt(period)) on shipped WMA folds."""
    half = _weighted_moving_average(values, period // 2)
    full = _weighted_moving_average(values, period)
    two = Decimal(2)
    raw = tuple(
        None if short is None or long is None else two * short - long
        for short, long in zip(half, full, strict=True)
    )
    return _weighted_moving_average_optional(raw, isqrt(period))


def _kaufman_adaptive_values(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> tuple[Decimal | None, ...]:
    """Return KAMA over the selected source.

    Seed: the source at bar ``period - 1`` (not reported). From bar ``period`` on:
    ``er = |x - x[period]| / sum(|x - x[1]|)`` over the last ``period`` changes (a zero
    sum, i.e. a flat window, is ``er = 1`` as in TA-Lib, so KAMA snaps to the flat price);
    ``sc = (er * (fast_sc - slow_sc) + slow_sc)**2`` with ``fast_sc = 2 / (fast_period + 1)``
    and ``slow_sc = 2 / (slow_period + 1)``; ``kama = previous + sc * (x - previous)``.
    """
    parameters = _parameters_of(indicator, KamaIndicatorParameters)
    values = _locked_source_series(indicator, candles)
    period = parameters.period
    result: list[Decimal | None] = [None] * min(period, len(values))
    if len(values) <= period:
        return tuple(result)
    fast = Decimal(2) / Decimal(parameters.fast_period + 1)
    slow = Decimal(2) / Decimal(parameters.slow_period + 1)
    span = fast - slow
    previous = values[period - 1]
    for index in range(period, len(values)):
        change = abs(values[index] - values[index - period])
        volatility = Decimal(0)
        for position in range(index - period + 1, index + 1):
            volatility += abs(values[position] - values[position - 1])
        efficiency = Decimal(1) if volatility == 0 else change / volatility
        base = efficiency * span + slow
        smoothing = base * base
        previous = previous + smoothing * (values[index] - previous)
        result.append(previous)
    return tuple(result)
