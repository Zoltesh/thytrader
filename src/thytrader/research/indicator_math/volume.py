"""Volume indicators (MFI, VWMA, OBV, accumulation/distribution, CMF, VWAP, force index)."""

from __future__ import annotations

from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING

from thytrader.research.indicator_math.core import _parameters_of
from thytrader.research.indicator_math.moving_averages import (
    _exponential_moving_average_optional,
    _simple_moving_average,
)
from thytrader.strategies.models import SignalLineIndicatorParameters

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import IndicatorDefinition


def _money_flow_index(
    candles: Sequence[Candle],
    period: int,
) -> tuple[Decimal | None, ...]:
    """Return MFI from typical-price money flow over ``period`` signed changes."""
    hundred = Decimal(100)
    three = Decimal(3)
    typical = tuple((candle.high + candle.low + candle.close) / three for candle in candles)
    result: list[Decimal | None] = []
    for index in range(len(candles)):
        if index < period:
            result.append(None)
            continue
        positive = Decimal(0)
        negative = Decimal(0)
        window_start = index - period + 1
        for flow_index in range(window_start, index + 1):
            current = typical[flow_index]
            previous = typical[flow_index - 1]
            raw = current * candles[flow_index].volume
            if current > previous:
                positive += raw
            elif current < previous:
                negative += raw
        total = positive + negative
        if total == 0:
            result.append(None)
            continue
        result.append(hundred * positive / total)
    return tuple(result)


def _money_flow_multiplier(candle: Candle) -> Decimal:
    """Return ``((close - low) - (high - close)) / (high - low)``; a zero range is ``0``."""
    span = candle.high - candle.low
    if span == 0:
        return Decimal(0)
    return ((candle.close - candle.low) - (candle.high - candle.close)) / span


def _volume_weighted_moving_average(
    candles: Sequence[Candle],
    period: int,
) -> tuple[Decimal | None, ...]:
    """Return sum(close * volume) / sum(volume) over the window; zero volume is undefined."""
    result: list[Decimal | None] = []
    for index in range(len(candles)):
        if index + 1 < period:
            result.append(None)
            continue
        weighted = Decimal(0)
        volume = Decimal(0)
        for candle in candles[index + 1 - period : index + 1]:
            weighted += candle.close * candle.volume
            volume += candle.volume
        result.append(None if volume == 0 else weighted / volume)
    return tuple(result)


def _on_balance_volume_series(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> dict[str, tuple[Decimal | None, ...]]:
    """Return OBV (0 on the first bar, then +/- volume on up/down closes) and its SMA.

    OBV is cumulative from the first supplied bar, so its level depends on where the
    series starts; the signal crossover and OBV differences do not.
    """
    parameters = _parameters_of(indicator, SignalLineIndicatorParameters)
    running = Decimal(0)
    balance: list[Decimal] = []
    previous_close: Decimal | None = None
    for candle in candles:
        if previous_close is not None:
            if candle.close > previous_close:
                running += candle.volume
            elif candle.close < previous_close:
                running -= candle.volume
        balance.append(running)
        previous_close = candle.close
    signal = _simple_moving_average(balance, parameters.signal_period)
    return {"obv": tuple(balance), "signal": signal}


def _accumulation_distribution_series(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> dict[str, tuple[Decimal | None, ...]]:
    """Return the cumulative A/D line (sum of multiplier * volume) and its SMA signal.

    A zero high-low range contributes 0. Like OBV, the level depends on the first bar.
    """
    parameters = _parameters_of(indicator, SignalLineIndicatorParameters)
    running = Decimal(0)
    line: list[Decimal] = []
    for candle in candles:
        running += _money_flow_multiplier(candle) * candle.volume
        line.append(running)
    signal = _simple_moving_average(line, parameters.signal_period)
    return {"ad": tuple(line), "signal": signal}


def _chaikin_money_flow(candles: Sequence[Candle], period: int) -> tuple[Decimal | None, ...]:
    """Return sum(multiplier * volume) / sum(volume) over the window; zero volume undefined."""
    flows = tuple(_money_flow_multiplier(candle) * candle.volume for candle in candles)
    result: list[Decimal | None] = []
    for index in range(len(candles)):
        if index + 1 < period:
            result.append(None)
            continue
        flow_sum = Decimal(0)
        volume = Decimal(0)
        for position in range(index + 1 - period, index + 1):
            flow_sum += flows[position]
            volume += candles[position].volume
        result.append(None if volume == 0 else flow_sum / volume)
    return tuple(result)


def _rolling_volume_weighted_average_price(
    candles: Sequence[Candle],
    period: int,
) -> tuple[Decimal | None, ...]:
    """Return sum(typical * volume) / sum(volume), typical = (high + low + close) / 3."""
    three = Decimal(3)
    typical = tuple((candle.high + candle.low + candle.close) / three for candle in candles)
    result: list[Decimal | None] = []
    for index in range(len(candles)):
        if index + 1 < period:
            result.append(None)
            continue
        weighted = Decimal(0)
        volume = Decimal(0)
        for position in range(index + 1 - period, index + 1):
            weighted += typical[position] * candles[position].volume
            volume += candles[position].volume
        result.append(None if volume == 0 else weighted / volume)
    return tuple(result)


def _force_index(candles: Sequence[Candle], period: int) -> tuple[Decimal | None, ...]:
    """Return the shipped EMA of (close - prior close) * volume, seeded from bar 1."""
    raw: list[Decimal | None] = [None] if candles else []
    for previous, candle in pairwise(candles):
        raw.append((candle.close - previous.close) * candle.volume)
    return _exponential_moving_average_optional(raw, period)
