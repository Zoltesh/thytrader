"""Deterministic Decimal indicator calculations for signal evaluation."""

from __future__ import annotations

from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from typing import TYPE_CHECKING

from thytrader.strategies.models import IndicatorKind

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import IndicatorDefinition

_ENGINE_CONTEXT = Context(
    prec=64,
    rounding=ROUND_HALF_EVEN,
    Emin=-6143,
    Emax=6144,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)


class IndicatorCalculationError(ValueError):
    """Report unsupported or invalid deterministic indicator input."""


def calculate_indicator_rows(
    indicators: Sequence[IndicatorDefinition],
    candles: Sequence[Candle],
) -> tuple[dict[str, Decimal | None], ...]:
    """Calculate each declared indicator sequentially for every supplied candle."""
    rows = [dict[str, Decimal | None]() for _candle in candles]
    with localcontext(_ENGINE_CONTEXT):
        for indicator in indicators:
            values = _indicator_values(indicator, candles)
            for row, value in zip(rows, values, strict=True):
                row[indicator.id] = value
    return tuple(rows)


def canonical_decimal(value: Decimal) -> str:
    """Render one finite engine Decimal without exponent notation or trailing zeros."""
    text = format(value, "f")
    whole, separator, fraction = text.partition(".")
    canonical_fraction = fraction.rstrip("0") if separator else ""
    decimal_places = f".{canonical_fraction}" if canonical_fraction else ""
    result = f"{whole}{decimal_places}"
    return "0" if Decimal(result).is_zero() else result


def _indicator_values(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> tuple[Decimal | None, ...]:
    """Dispatch one declarative definition to its exact implemented calculation."""
    if indicator.kind in {IndicatorKind.SMA, IndicatorKind.VOLUME_SMA}:
        values = (
            tuple(candle.close for candle in candles)
            if indicator.kind is IndicatorKind.SMA
            else tuple(candle.volume for candle in candles)
        )
        return _simple_moving_average(values, indicator.parameters.period)
    if indicator.kind is IndicatorKind.ATR:
        return _average_true_range(candles, indicator.parameters.period)
    if indicator.kind is IndicatorKind.EMA:
        return _exponential_moving_average(
            tuple(candle.close for candle in candles),
            indicator.parameters.period,
        )
    if indicator.kind is IndicatorKind.RSI:
        return _relative_strength_index(
            tuple(candle.close for candle in candles),
            indicator.parameters.period,
        )
    raise IndicatorCalculationError(
        f"Indicator kind {indicator.kind.value} is not implemented by this engine contract."
    )


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


def _relative_strength_index(
    values: Sequence[Decimal],
    period: int,
) -> tuple[Decimal | None, ...]:
    """Return Wilder RSI after exactly ``period`` completed price changes."""
    result: list[Decimal | None] = [None]
    gains: list[Decimal] = []
    losses: list[Decimal] = []
    average_gain: Decimal | None = None
    average_loss: Decimal | None = None
    for index in range(1, len(values)):
        change = values[index] - values[index - 1]
        gain = max(change, Decimal(0))
        loss = max(-change, Decimal(0))
        gains.append(gain)
        losses.append(loss)
        if index < period:
            result.append(None)
            continue
        if average_gain is None or average_loss is None:
            average_gain = sum(gains[:period], start=Decimal(0)) / Decimal(period)
            average_loss = sum(losses[:period], start=Decimal(0)) / Decimal(period)
        else:
            average_gain = (average_gain * Decimal(period - 1) + gain) / Decimal(period)
            average_loss = (average_loss * Decimal(period - 1) + loss) / Decimal(period)
        if average_gain == 0 and average_loss == 0:
            value = Decimal(50)
        elif average_loss == 0:
            value = Decimal(100)
        else:
            value = Decimal(100) * average_gain / (average_gain + average_loss)
        result.append(value)
    return tuple(result)


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
