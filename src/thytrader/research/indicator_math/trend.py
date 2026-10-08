"""Directional and trend systems (ADX, Supertrend, Parabolic SAR, Aroon, Ichimoku, Vortex)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.research.indicator_math.core import (
    IndicatorCalculationError,
    _parameters_of,
    _period_parameter,
)
from thytrader.research.indicator_math.moving_averages import (
    _wilder_smooth,
    _wilder_smooth_optional,
)
from thytrader.research.indicator_math.rolling_statistics import _rolling_extreme
from thytrader.research.indicator_math.volatility import _average_true_range, _true_range
from thytrader.strategies.models import (
    IchimokuIndicatorParameters,
    IndicatorParameters,
    ParabolicSarIndicatorParameters,
    SupertrendIndicatorParameters,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import IndicatorDefinition


def _adx_series(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> dict[str, tuple[Decimal | None, ...]]:
    """Return Wilder +DI, -DI, and ADX using the shipped ATR seed and recurrence."""
    parameters = indicator.parameters
    if not isinstance(parameters, IndicatorParameters):
        raise IndicatorCalculationError(
            f"Indicator kind {indicator.kind.value} is not implemented by this engine contract."
        )
    period = parameters.period
    hundred = Decimal(100)
    true_ranges: list[Decimal] = []
    plus_dm: list[Decimal] = []
    minus_dm: list[Decimal] = []
    previous_high: Decimal | None = None
    previous_low: Decimal | None = None
    previous_close: Decimal | None = None
    for candle in candles:
        high_low = candle.high - candle.low
        if previous_close is None or previous_high is None or previous_low is None:
            true_ranges.append(high_low)
            plus_dm.append(Decimal(0))
            minus_dm.append(Decimal(0))
        else:
            true_ranges.append(
                max(
                    high_low,
                    abs(candle.high - previous_close),
                    abs(candle.low - previous_close),
                )
            )
            up_move = candle.high - previous_high
            down_move = previous_low - candle.low
            plus_dm.append(up_move if up_move > down_move and up_move > 0 else Decimal(0))
            minus_dm.append(down_move if down_move > up_move and down_move > 0 else Decimal(0))
        previous_high = candle.high
        previous_low = candle.low
        previous_close = candle.close
    smoothed_tr = _wilder_smooth(true_ranges, period)
    smoothed_plus = _wilder_smooth(plus_dm, period)
    smoothed_minus = _wilder_smooth(minus_dm, period)
    plus_di: list[Decimal | None] = []
    minus_di: list[Decimal | None] = []
    dx_values: list[Decimal | None] = []
    for tr_value, plus_value, minus_value in zip(
        smoothed_tr, smoothed_plus, smoothed_minus, strict=True
    ):
        if tr_value is None or plus_value is None or minus_value is None or tr_value == 0:
            plus_di.append(None)
            minus_di.append(None)
            dx_values.append(None)
            continue
        plus_line = hundred * plus_value / tr_value
        minus_line = hundred * minus_value / tr_value
        plus_di.append(plus_line)
        minus_di.append(minus_line)
        di_sum = plus_line + minus_line
        if di_sum == 0:
            dx_values.append(None)
            continue
        dx_values.append(hundred * abs(plus_line - minus_line) / di_sum)
    adx_values = _wilder_smooth_optional(tuple(dx_values), period)
    return {
        "adx": adx_values,
        "plus_di": tuple(plus_di),
        "minus_di": tuple(minus_di),
    }


@dataclass(slots=True)
class _SupertrendState:
    """Final bands and trend carried from the previous bar."""

    upper: Decimal
    lower: Decimal
    direction: int


def _supertrend_series(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> dict[str, tuple[Decimal | None, ...]]:
    """Return Supertrend ``value`` and ``direction`` (1 up, -1 down) on the shipped ATR.

    Bands are ``(high + low) / 2 +/- multiplier * ATR``. The first ATR bar seeds final
    bands with the basic bands and a down trend (value = upper band). Later final bands
    only tighten unless the previous close broke them; the trend flips up when close
    exceeds the final upper band and down when close falls below the final lower band.
    """
    parameters = _parameters_of(indicator, SupertrendIndicatorParameters)
    atr = _average_true_range(candles, parameters.period)
    multiplier = Decimal(parameters.multiplier)
    two = Decimal(2)
    values: list[Decimal | None] = []
    directions: list[Decimal | None] = []
    state: _SupertrendState | None = None
    previous_close: Decimal | None = None
    for candle, atr_value in zip(candles, atr, strict=True):
        if atr_value is None:
            values.append(None)
            directions.append(None)
            previous_close = candle.close
            continue
        midpoint = (candle.high + candle.low) / two
        width = multiplier * atr_value
        if state is None or previous_close is None:
            state = _SupertrendState(upper=midpoint + width, lower=midpoint - width, direction=-1)
        else:
            _advance_supertrend(state, candle.close, previous_close, midpoint, width)
        values.append(state.lower if state.direction > 0 else state.upper)
        directions.append(Decimal(state.direction))
        previous_close = candle.close
    return {"value": tuple(values), "direction": tuple(directions)}


def _advance_supertrend(
    state: _SupertrendState,
    close: Decimal,
    previous_close: Decimal,
    midpoint: Decimal,
    width: Decimal,
) -> None:
    """Move final bands and the trend forward by one completed bar."""
    basic_upper = midpoint + width
    basic_lower = midpoint - width
    if basic_upper < state.upper or previous_close > state.upper:
        state.upper = basic_upper
    if basic_lower > state.lower or previous_close < state.lower:
        state.lower = basic_lower
    if state.direction < 0:
        state.direction = 1 if close > state.upper else -1
    else:
        state.direction = -1 if close < state.lower else 1


@dataclass(slots=True)
class _SarState:
    """Wilder SAR position, extreme point, and acceleration."""

    is_long: bool
    sar: Decimal
    extreme: Decimal
    acceleration: Decimal


def _parabolic_sar_values(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> tuple[Decimal | None, ...]:
    """Return Wilder Parabolic SAR following the TA-Lib ``SAR`` recurrence in Decimal.

    The trend starts short only when bar 1's down-move (prior low - low) is positive and
    larger than its up-move; otherwise long, with SAR at bar 0's low (short: high) and
    the extreme at bar 1's high (short: low). The first SAR is reported on bar 1.
    """
    parameters = _parameters_of(indicator, ParabolicSarIndicatorParameters)
    if not candles:
        return ()
    if len(candles) == 1:
        return (None,)
    step = Decimal(parameters.step)
    max_step = Decimal(parameters.max_step)
    first, second = candles[0], candles[1]
    down_move = first.low - second.low
    up_move = second.high - first.high
    is_long = not (down_move > 0 and up_move < down_move)
    state = _SarState(
        is_long=is_long,
        sar=first.low if is_long else first.high,
        extreme=second.high if is_long else second.low,
        acceleration=step,
    )
    result: list[Decimal | None] = [None]
    previous_high, previous_low = second.high, second.low
    for candle in candles[1:]:
        if state.is_long:
            result.append(
                _advance_long_sar(state, candle, previous_high, previous_low, step, max_step)
            )
        else:
            result.append(
                _advance_short_sar(state, candle, previous_high, previous_low, step, max_step)
            )
        previous_high, previous_low = candle.high, candle.low
    return tuple(result)


def _advance_long_sar(
    state: _SarState,
    candle: Candle,
    previous_high: Decimal,
    previous_low: Decimal,
    step: Decimal,
    max_step: Decimal,
) -> Decimal:
    """Report this bar's SAR for a long trend, reversing when the low touches it."""
    if candle.low <= state.sar:
        state.is_long = False
        reported = max(state.extreme, previous_high, candle.high)
        state.acceleration = step
        state.extreme = candle.low
        following = reported + state.acceleration * (state.extreme - reported)
        state.sar = max(following, previous_high, candle.high)
        return reported
    reported = state.sar
    if candle.high > state.extreme:
        state.extreme = candle.high
        state.acceleration = min(state.acceleration + step, max_step)
    following = state.sar + state.acceleration * (state.extreme - state.sar)
    state.sar = min(following, previous_low, candle.low)
    return reported


def _advance_short_sar(
    state: _SarState,
    candle: Candle,
    previous_high: Decimal,
    previous_low: Decimal,
    step: Decimal,
    max_step: Decimal,
) -> Decimal:
    """Report this bar's SAR for a short trend, reversing when the high touches it."""
    if candle.high >= state.sar:
        state.is_long = True
        reported = min(state.extreme, previous_low, candle.low)
        state.acceleration = step
        state.extreme = candle.high
        following = reported + state.acceleration * (state.extreme - reported)
        state.sar = min(following, previous_low, candle.low)
        return reported
    reported = state.sar
    if candle.low < state.extreme:
        state.extreme = candle.low
        state.acceleration = min(state.acceleration + step, max_step)
    following = state.sar + state.acceleration * (state.extreme - state.sar)
    state.sar = max(following, previous_high, candle.high)
    return reported


def _aroon_series(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> dict[str, tuple[Decimal | None, ...]]:
    """Return Aroon up/down over the last ``period + 1`` bars and their difference.

    ``up = 100 * (period - bars since the highest high) / period``; ties pick the most
    recent bar, so a flat window reads 100 for both lines and 0 for the oscillator.
    """
    period = _period_parameter(indicator)
    hundred = Decimal(100)
    divisor = Decimal(period)
    up: list[Decimal | None] = []
    down: list[Decimal | None] = []
    oscillator: list[Decimal | None] = []
    for index in range(len(candles)):
        if index < period:
            up.append(None)
            down.append(None)
            oscillator.append(None)
            continue
        high_index = low_index = index - period
        for position in range(index - period + 1, index + 1):
            if candles[position].high >= candles[high_index].high:
                high_index = position
            if candles[position].low <= candles[low_index].low:
                low_index = position
        up_value = hundred * Decimal(period - (index - high_index)) / divisor
        down_value = hundred * Decimal(period - (index - low_index)) / divisor
        up.append(up_value)
        down.append(down_value)
        oscillator.append(up_value - down_value)
    return {"up": tuple(up), "down": tuple(down), "oscillator": tuple(oscillator)}


def _midpoint_series(candles: Sequence[Candle], period: int) -> tuple[Decimal | None, ...]:
    """Return (highest high + lowest low) / 2 over each inclusive window."""
    highest = _rolling_extreme(tuple(candle.high for candle in candles), period, maximum=True)
    lowest = _rolling_extreme(tuple(candle.low for candle in candles), period, maximum=False)
    two = Decimal(2)
    return tuple(
        None if high is None or low is None else (high + low) / two
        for high, low in zip(highest, lowest, strict=True)
    )


def _ichimoku_series(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> dict[str, tuple[Decimal | None, ...]]:
    """Return Ichimoku lines on the bar their data ends, with no forward displacement.

    ``senkou_a`` and ``senkou_b`` are what a chart plots ``kijun_period`` bars ahead;
    here they are reported on the bar that produced them. Chikou is omitted.
    """
    parameters = _parameters_of(indicator, IchimokuIndicatorParameters)
    tenkan = _midpoint_series(candles, parameters.tenkan_period)
    kijun = _midpoint_series(candles, parameters.kijun_period)
    two = Decimal(2)
    senkou_a = tuple(
        None if conversion is None or base is None else (conversion + base) / two
        for conversion, base in zip(tenkan, kijun, strict=True)
    )
    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a": senkou_a,
        "senkou_b": _midpoint_series(candles, parameters.senkou_b_period),
    }


def _vortex_series(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> dict[str, tuple[Decimal | None, ...]]:
    """Return VI+ = sum|high - prior low| / sum TR and VI- = sum|low - prior high| / sum TR.

    Each sum covers the last ``period`` bars that have a previous bar; a zero true-range
    sum (a perfectly flat window) is undefined.
    """
    period = _period_parameter(indicator)
    plus: list[Decimal | None] = []
    minus: list[Decimal | None] = []
    for index in range(len(candles)):
        if index < period:
            plus.append(None)
            minus.append(None)
            continue
        plus_sum = Decimal(0)
        minus_sum = Decimal(0)
        range_sum = Decimal(0)
        for position in range(index - period + 1, index + 1):
            current = candles[position]
            previous = candles[position - 1]
            plus_sum += abs(current.high - previous.low)
            minus_sum += abs(current.low - previous.high)
            range_sum += _true_range(current, previous.close)
        if range_sum == 0:
            plus.append(None)
            minus.append(None)
            continue
        plus.append(plus_sum / range_sum)
        minus.append(minus_sum / range_sum)
    return {"plus": tuple(plus), "minus": tuple(minus)}
