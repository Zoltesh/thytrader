"""Deterministic Decimal indicator calculations for signal evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from itertools import pairwise
from math import isqrt
from typing import TYPE_CHECKING

from pydantic import BaseModel

from thytrader.strategies.models import (
    AwesomeOscillatorIndicatorParameters,
    BollingerIndicatorParameters,
    ConstantIndicatorParameters,
    HistoricalVolatilityIndicatorParameters,
    IchimokuIndicatorParameters,
    IndicatorKind,
    IndicatorParameters,
    KamaIndicatorParameters,
    KeltnerIndicatorParameters,
    MacdIndicatorParameters,
    ParabolicSarIndicatorParameters,
    SignalLineIndicatorParameters,
    StochasticIndicatorParameters,
    StochasticRsiIndicatorParameters,
    SupertrendIndicatorParameters,
    TsiIndicatorParameters,
    UltimateOscillatorIndicatorParameters,
    indicator_offset,
    indicator_output_series,
    indicator_value_keys,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import IndicatorDefinition

_ENGINE_CONTEXT = Context(
    prec=64,
    rounding=ROUND_HALF_EVEN,
    Emin=-6143,
    Emax=6144,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)
_FLAT_RSI_RANGE = Decimal("1e-30")
"""Stochastic RSI treats an RSI range at or below this as zero (64-digit rounding noise)."""


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
            keyed_rows = _keyed_indicator_values(indicator, candles)
            for row, keyed in zip(rows, keyed_rows, strict=True):
                row.update(keyed)
    return tuple(rows)


def canonical_decimal(value: Decimal) -> str:
    """Render one finite engine Decimal without exponent notation or trailing zeros."""
    text = format(value, "f")
    whole, separator, fraction = text.partition(".")
    canonical_fraction = fraction.rstrip("0") if separator else ""
    decimal_places = f".{canonical_fraction}" if canonical_fraction else ""
    result = f"{whole}{decimal_places}"
    return "0" if Decimal(result).is_zero() else result


def _keyed_indicator_values(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> tuple[dict[str, Decimal | None], ...]:
    """Return one mapping of output keys to values for every supplied candle.

    A declared ``offset`` shifts every output series ``offset`` completed bars later:
    row ``i`` carries the unlagged row ``i - offset`` and the first ``offset`` rows are
    undefined. Only earlier bars are read, so a lag can never look ahead.
    """
    rows = _unlagged_keyed_values(indicator, candles)
    offset = indicator_offset(indicator)
    if offset == 0:
        return rows
    lead = min(offset, len(rows))
    undefined = dict.fromkeys(indicator_value_keys(indicator))
    return tuple(dict(undefined) for _index in range(lead)) + rows[: len(rows) - lead]


def _unlagged_keyed_values(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> tuple[dict[str, Decimal | None], ...]:
    """Return keyed values for every candle before any declared bar lag is applied."""
    series = indicator_output_series(indicator.kind)
    if series is None:
        values = _indicator_values(indicator, candles)
        return tuple({indicator.id: value} for value in values)
    matrix = _multi_series_values(indicator, candles)
    return tuple(
        {f"{indicator.id}.{name}": matrix[name][index] for name in series}
        for index in range(len(candles))
    )


def _multi_series_values(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> dict[str, tuple[Decimal | None, ...]]:
    """Dispatch one multi-output definition to its exact implemented series."""
    calculator = _MULTI_SERIES_CALCULATORS.get(indicator.kind)
    if calculator is None:
        raise IndicatorCalculationError(
            f"Indicator kind {indicator.kind.value} is not implemented by this engine contract."
        )
    return calculator(indicator, candles)


def _macd_series(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> dict[str, tuple[Decimal | None, ...]]:
    """Return MACD line, signal, and histogram from shipped EMA recurrences."""
    parameters = indicator.parameters
    if not isinstance(parameters, MacdIndicatorParameters):
        raise IndicatorCalculationError(
            f"Indicator kind {indicator.kind.value} is not implemented by this engine contract."
        )
    closes = _locked_source_series(indicator, candles)
    fast = _exponential_moving_average(closes, parameters.fast_period)
    slow = _exponential_moving_average(closes, parameters.slow_period)
    macd_line = tuple(
        None if fast_value is None or slow_value is None else fast_value - slow_value
        for fast_value, slow_value in zip(fast, slow, strict=True)
    )
    signal = _macd_signal_ema(macd_line, parameters.signal_period)
    histogram = tuple(
        None if line is None or signal_value is None else line - signal_value
        for line, signal_value in zip(macd_line, signal, strict=True)
    )
    return {"macd": macd_line, "signal": signal, "histogram": histogram}


def _macd_signal_ema(
    macd_line: Sequence[Decimal | None],
    period: int,
) -> tuple[Decimal | None, ...]:
    """Smooth the defined MACD-line suffix with the shipped SMA-seeded EMA."""
    defined = tuple(value for value in macd_line if value is not None)
    prefix = (None,) * (len(macd_line) - len(defined))
    return prefix + _exponential_moving_average(defined, period)


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


def _stochastic_series(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> dict[str, tuple[Decimal | None, ...]]:
    """Return fast stochastic %K and SMA %D from the inclusive HLC window."""
    parameters = indicator.parameters
    if not isinstance(parameters, StochasticIndicatorParameters):
        raise IndicatorCalculationError(
            f"Indicator kind {indicator.kind.value} is not implemented by this engine contract."
        )
    highest = _rolling_extreme(
        tuple(candle.high for candle in candles),
        parameters.k_period,
        maximum=True,
    )
    lowest = _rolling_extreme(
        tuple(candle.low for candle in candles),
        parameters.k_period,
        maximum=False,
    )
    hundred = Decimal(100)
    percent_k: list[Decimal | None] = []
    for candle, high_value, low_value in zip(candles, highest, lowest, strict=True):
        if high_value is None or low_value is None:
            percent_k.append(None)
            continue
        span = high_value - low_value
        if span == 0:
            percent_k.append(None)
            continue
        percent_k.append(hundred * (candle.close - low_value) / span)
    percent_d = _simple_moving_average_defined(tuple(percent_k), parameters.d_period)
    return {"k": tuple(percent_k), "d": percent_d}


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


def _indicator_values(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> tuple[Decimal | None, ...]:
    """Dispatch one declarative definition to its exact implemented calculation."""
    if indicator.kind is IndicatorKind.IDENTITY:
        return _identity_values(indicator, candles)
    if indicator.kind is IndicatorKind.CONSTANT:
        return _constant_values(indicator, candles)
    shaped_calculator = _SHAPED_CALCULATORS.get(indicator.kind)
    if shaped_calculator is not None:
        return shaped_calculator(indicator, candles)
    parameters = indicator.parameters
    if not isinstance(parameters, IndicatorParameters):
        raise IndicatorCalculationError(
            f"Indicator kind {indicator.kind.value} is not implemented by this engine contract."
        )
    period = parameters.period
    candle_calculator = _CANDLE_CALCULATORS.get(indicator.kind)
    if candle_calculator is not None:
        return candle_calculator(candles, period)
    series = _locked_source_series(indicator, candles)
    series_calculator = _SERIES_CALCULATORS.get(indicator.kind)
    if series_calculator is not None:
        return series_calculator(series, period)
    raise IndicatorCalculationError(
        f"Indicator kind {indicator.kind.value} is not implemented by this engine contract."
    )


def _identity_values(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> tuple[Decimal | None, ...]:
    """Return the selected OHLCV field on every completed bar."""
    return _locked_source_series(indicator, candles)


def _constant_values(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> tuple[Decimal | None, ...]:
    """Repeat the declared finite level on every completed bar."""
    parameters = indicator.parameters
    if not isinstance(parameters, ConstantIndicatorParameters):
        raise IndicatorCalculationError(
            f"Indicator kind {indicator.kind.value} is not implemented by this engine contract."
        )
    value = Decimal(parameters.value)
    return tuple(value for _candle in candles)


def _locked_source_series(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> tuple[Decimal, ...]:
    """Return the single OHLCV field selected by one identity or locked source kind."""
    source = indicator.input
    if source == "open":
        return tuple(candle.open for candle in candles)
    if source == "close":
        return tuple(candle.close for candle in candles)
    if source == "volume":
        return tuple(candle.volume for candle in candles)
    if source == "high":
        return tuple(candle.high for candle in candles)
    if source == "low":
        return tuple(candle.low for candle in candles)
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
    if not values:
        return ()
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


def _rate_of_change(
    values: Sequence[Decimal],
    period: int,
) -> tuple[Decimal | None, ...]:
    """Return percent change versus the close exactly ``period`` bars ago."""
    result: list[Decimal | None] = []
    hundred = Decimal(100)
    for index, value in enumerate(values):
        if index < period:
            result.append(None)
            continue
        past = values[index - period]
        if past == 0:
            result.append(None)
            continue
        result.append(hundred * (value - past) / past)
    return tuple(result)


def _williams_percent_r(
    candles: Sequence[Candle],
    period: int,
) -> tuple[Decimal | None, ...]:
    """Return Williams %R from the inclusive high/low window and current close."""
    highest = _rolling_extreme(tuple(candle.high for candle in candles), period, maximum=True)
    lowest = _rolling_extreme(tuple(candle.low for candle in candles), period, maximum=False)
    minus_hundred = Decimal(-100)
    result: list[Decimal | None] = []
    for candle, high_value, low_value in zip(candles, highest, lowest, strict=True):
        if high_value is None or low_value is None:
            result.append(None)
            continue
        span = high_value - low_value
        if span == 0:
            result.append(None)
            continue
        result.append((high_value - candle.close) / span * minus_hundred)
    return tuple(result)


def _commodity_channel_index(
    candles: Sequence[Candle],
    period: int,
) -> tuple[Decimal | None, ...]:
    """Return CCI from typical price, shipped SMA, and population mean deviation."""
    typical = tuple((candle.high + candle.low + candle.close) / Decimal(3) for candle in candles)
    means = _simple_moving_average(typical, period)
    period_decimal = Decimal(period)
    lambert = Decimal("0.015")
    result: list[Decimal | None] = []
    for index, mean in enumerate(means):
        if mean is None:
            result.append(None)
            continue
        window = typical[index + 1 - period : index + 1]
        mad = sum((abs(value - mean) for value in window), start=Decimal(0)) / period_decimal
        if mad == 0:
            result.append(None)
            continue
        result.append((typical[index] - mean) / (lambert * mad))
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


def _momentum(
    values: Sequence[Decimal],
    period: int,
) -> tuple[Decimal | None, ...]:
    """Return close minus the close exactly ``period`` bars ago."""
    result: list[Decimal | None] = []
    for index, value in enumerate(values):
        if index < period:
            result.append(None)
            continue
        result.append(value - values[index - period])
    return tuple(result)


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


def _rolling_highest(values: Sequence[Decimal], period: int) -> tuple[Decimal | None, ...]:
    """Return a rolling max after exactly one full inclusive window."""
    return _rolling_extreme(values, period, maximum=True)


def _rolling_lowest(values: Sequence[Decimal], period: int) -> tuple[Decimal | None, ...]:
    """Return a rolling min after exactly one full inclusive window."""
    return _rolling_extreme(values, period, maximum=False)


def _parameters_of[ParametersT: BaseModel](
    indicator: IndicatorDefinition,
    model: type[ParametersT],
) -> ParametersT:
    """Return the validated parameter block, or fail closed when the shape is unexpected."""
    parameters = indicator.parameters
    if not isinstance(parameters, model):
        raise IndicatorCalculationError(
            f"Indicator kind {indicator.kind.value} is not implemented by this engine contract."
        )
    return parameters


def _period_parameter(indicator: IndicatorDefinition) -> int:
    """Return ``period`` from a plain or annualized-volatility parameter block."""
    parameters = indicator.parameters
    if isinstance(parameters, HistoricalVolatilityIndicatorParameters):
        return parameters.period
    return _parameters_of(indicator, IndicatorParameters).period


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


def _windows_defined(
    values: Sequence[Decimal | None],
    period: int,
) -> tuple[tuple[Decimal, ...] | None, ...]:
    """Return each inclusive window when it is complete and fully defined, else ``None``."""
    windows: list[tuple[Decimal, ...] | None] = []
    for index in range(len(values)):
        if index + 1 < period:
            windows.append(None)
            continue
        window = values[index + 1 - period : index + 1]
        defined = tuple(value for value in window if value is not None)
        windows.append(defined if len(defined) == period else None)
    return tuple(windows)


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


def _difference(
    left: Sequence[Decimal | None],
    right: Sequence[Decimal | None],
) -> tuple[Decimal | None, ...]:
    """Return ``left - right`` per bar, undefined when either side is undefined."""
    return tuple(
        None if first is None or second is None else first - second
        for first, second in zip(left, right, strict=True)
    )


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


def _money_flow_multiplier(candle: Candle) -> Decimal:
    """Return ``((close - low) - (high - close)) / (high - low)``; a zero range is ``0``."""
    span = candle.high - candle.low
    if span == 0:
        return Decimal(0)
    return ((candle.close - candle.low) - (candle.high - candle.close)) / span


# --- Trend -------------------------------------------------------------------------------


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


def _triple_exponential_rate(values: Sequence[Decimal], period: int) -> tuple[Decimal | None, ...]:
    """Return TRIX = 100 * (E3 - E3[1]) / E3[1] for E3 = EMA(EMA(EMA(close)))."""
    first = _exponential_moving_average(values, period)
    second = _exponential_moving_average_optional(first, period)
    third = _exponential_moving_average_optional(second, period)
    hundred = Decimal(100)
    result: list[Decimal | None] = []
    for index, current in enumerate(third):
        previous = third[index - 1] if index else None
        if current is None or previous is None or previous == 0:
            result.append(None)
            continue
        result.append(hundred * (current - previous) / previous)
    return tuple(result)


# --- Momentum ----------------------------------------------------------------------------


def _stochastic_rsi_series(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> dict[str, tuple[Decimal | None, ...]]:
    """Return stochastic RSI %K and %D.

    ``raw = 100 * (RSI - min RSI) / (max RSI - min RSI)`` over ``stoch_period`` shipped RSI
    values; ``k`` is the SMA of ``raw`` over ``k_period`` and ``d`` the SMA of ``k`` over
    ``d_period``. Any undefined value in an SMA window is undefined. An RSI range at or
    below ``1e-30`` counts as zero and is undefined: on flat prices both Wilder averages
    decay together, so RSI is constant up to 64-digit rounding, and dividing by that
    rounding noise would turn it into an arbitrary 0-100 reading.
    """
    parameters = _parameters_of(indicator, StochasticRsiIndicatorParameters)
    closes = _locked_source_series(indicator, candles)
    rsi = _relative_strength_index(closes, parameters.rsi_period)
    highest = _rolling_extreme_optional(rsi, parameters.stoch_period, maximum=True)
    lowest = _rolling_extreme_optional(rsi, parameters.stoch_period, maximum=False)
    hundred = Decimal(100)
    raw: list[Decimal | None] = []
    for value, high, low in zip(rsi, highest, lowest, strict=True):
        if value is None or high is None or low is None or high - low <= _FLAT_RSI_RANGE:
            raw.append(None)
            continue
        raw.append(hundred * (value - low) / (high - low))
    percent_k = _simple_moving_average_defined(tuple(raw), parameters.k_period)
    percent_d = _simple_moving_average_defined(percent_k, parameters.d_period)
    return {"k": percent_k, "d": percent_d}


def _ppo_series(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> dict[str, tuple[Decimal | None, ...]]:
    """Return PPO = 100 * (EMA fast - EMA slow) / EMA slow, its EMA signal, and histogram."""
    parameters = _parameters_of(indicator, MacdIndicatorParameters)
    closes = _locked_source_series(indicator, candles)
    fast = _exponential_moving_average(closes, parameters.fast_period)
    slow = _exponential_moving_average(closes, parameters.slow_period)
    hundred = Decimal(100)
    line = tuple(
        None
        if fast_value is None or slow_value is None or slow_value == 0
        else hundred * (fast_value - slow_value) / slow_value
        for fast_value, slow_value in zip(fast, slow, strict=True)
    )
    signal = _exponential_moving_average_optional(line, parameters.signal_period)
    return {"ppo": line, "signal": signal, "histogram": _difference(line, signal)}


def _ultimate_oscillator_values(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> tuple[Decimal | None, ...]:
    """Return Williams' Ultimate Oscillator.

    For bars with a previous close: ``BP = close - min(low, prior close)`` and
    ``TR = max(high, prior close) - min(low, prior close)``. Each window average is
    ``sum BP / sum TR``; ``UO = 100 * (4 * short + 2 * medium + long) / 7``. A zero
    true-range sum in any window is undefined (TA-Lib instead drops that term).
    """
    parameters = _parameters_of(indicator, UltimateOscillatorIndicatorParameters)
    pressure: list[Decimal] = [Decimal(0)]
    ranges: list[Decimal] = [Decimal(0)]
    for previous, candle in pairwise(candles):
        true_low = min(candle.low, previous.close)
        pressure.append(candle.close - true_low)
        ranges.append(max(candle.high, previous.close) - true_low)
    hundred = Decimal(100)
    result: list[Decimal | None] = []
    for index in range(len(candles)):
        if index < parameters.long_period:
            result.append(None)
            continue
        averages = tuple(
            _pressure_average(pressure, ranges, index, period)
            for period in (
                parameters.short_period,
                parameters.medium_period,
                parameters.long_period,
            )
        )
        short, medium, long = averages
        if short is None or medium is None or long is None:
            result.append(None)
            continue
        result.append(hundred * (Decimal(4) * short + Decimal(2) * medium + long) / Decimal(7))
    return tuple(result)


def _pressure_average(
    pressure: Sequence[Decimal],
    ranges: Sequence[Decimal],
    index: int,
    period: int,
) -> Decimal | None:
    """Return the oldest-first ``sum BP / sum TR`` over ``period`` bars ending at ``index``."""
    pressure_sum = Decimal(0)
    range_sum = Decimal(0)
    for position in range(index - period + 1, index + 1):
        pressure_sum += pressure[position]
        range_sum += ranges[position]
    if range_sum == 0:
        return None
    return pressure_sum / range_sum


def _awesome_oscillator_values(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> tuple[Decimal | None, ...]:
    """Return SMA(median, fast) - SMA(median, slow) with median = (high + low) / 2."""
    parameters = _parameters_of(indicator, AwesomeOscillatorIndicatorParameters)
    two = Decimal(2)
    median = tuple((candle.high + candle.low) / two for candle in candles)
    return _difference(
        _simple_moving_average(median, parameters.fast_period),
        _simple_moving_average(median, parameters.slow_period),
    )


def _chande_momentum_oscillator(
    values: Sequence[Decimal],
    period: int,
) -> tuple[Decimal | None, ...]:
    """Return CMO = 100 * (gains - losses) / (gains + losses) over ``period`` changes.

    Gains and losses are plain oldest-first sums (Chande's definition, not Wilder
    smoothing). A window with no change at all is undefined.
    """
    hundred = Decimal(100)
    result: list[Decimal | None] = []
    for index in range(len(values)):
        if index < period:
            result.append(None)
            continue
        gains = Decimal(0)
        losses = Decimal(0)
        for position in range(index - period + 1, index + 1):
            change = values[position] - values[position - 1]
            if change > 0:
                gains += change
            elif change < 0:
                losses -= change
        total = gains + losses
        result.append(None if total == 0 else hundred * (gains - losses) / total)
    return tuple(result)


def _true_strength_series(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> dict[str, tuple[Decimal | None, ...]]:
    """Return TSI = 100 * EMA(EMA(change, long), short) / EMA(EMA(|change|, long), short).

    ``signal`` is the EMA of TSI over ``signal_period``. A zero denominator (no price
    change since the series began) is undefined.
    """
    parameters = _parameters_of(indicator, TsiIndicatorParameters)
    closes = _locked_source_series(indicator, candles)
    changes: list[Decimal | None] = [None] if closes else []
    magnitudes: list[Decimal | None] = [None] if closes else []
    for previous, current in pairwise(closes):
        changes.append(current - previous)
        magnitudes.append(abs(current - previous))
    numerator = _exponential_moving_average_optional(
        _exponential_moving_average_optional(changes, parameters.long_period),
        parameters.short_period,
    )
    denominator = _exponential_moving_average_optional(
        _exponential_moving_average_optional(magnitudes, parameters.long_period),
        parameters.short_period,
    )
    hundred = Decimal(100)
    tsi = tuple(
        None if top is None or bottom is None or bottom == 0 else hundred * top / bottom
        for top, bottom in zip(numerator, denominator, strict=True)
    )
    return {
        "tsi": tsi,
        "signal": _exponential_moving_average_optional(tsi, parameters.signal_period),
    }


# --- Volatility --------------------------------------------------------------------------


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


# --- Volume ------------------------------------------------------------------------------


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


# --- Statistical -------------------------------------------------------------------------


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


_CANDLE_CALCULATORS: dict[
    IndicatorKind,
    Callable[[Sequence[Candle], int], tuple[Decimal | None, ...]],
] = {
    IndicatorKind.ATR: _average_true_range,
    IndicatorKind.WILLIAMS_R: _williams_percent_r,
    IndicatorKind.CCI: _commodity_channel_index,
    IndicatorKind.MFI: _money_flow_index,
    IndicatorKind.VWMA: _volume_weighted_moving_average,
    IndicatorKind.NATR: _normalized_average_true_range,
    IndicatorKind.CHOPPINESS: _choppiness_index,
    IndicatorKind.CMF: _chaikin_money_flow,
    IndicatorKind.VWAP: _rolling_volume_weighted_average_price,
    IndicatorKind.FORCE_INDEX: _force_index,
}
"""Period kinds that read whole candles (more than one OHLCV field)."""

_SERIES_CALCULATORS: dict[
    IndicatorKind,
    Callable[[Sequence[Decimal], int], tuple[Decimal | None, ...]],
] = {
    IndicatorKind.SMA: _simple_moving_average,
    IndicatorKind.VOLUME_SMA: _simple_moving_average,
    IndicatorKind.EMA: _exponential_moving_average,
    IndicatorKind.RSI: _relative_strength_index,
    IndicatorKind.HIGHEST: _rolling_highest,
    IndicatorKind.LOWEST: _rolling_lowest,
    IndicatorKind.STDEV: _rolling_population_stdev,
    IndicatorKind.STDEV_SAMPLE: _rolling_sample_stdev,
    IndicatorKind.ROC: _rate_of_change,
    IndicatorKind.WMA: _weighted_moving_average,
    IndicatorKind.MOMENTUM: _momentum,
    IndicatorKind.DEMA: _double_exponential_moving_average,
    IndicatorKind.TEMA: _triple_exponential_moving_average,
    IndicatorKind.HMA: _hull_moving_average,
    IndicatorKind.TRIX: _triple_exponential_rate,
    IndicatorKind.CMO: _chande_momentum_oscillator,
    IndicatorKind.ZSCORE: _rolling_zscore,
    IndicatorKind.PERCENT_RANK: _percent_rank,
}
"""Period kinds over one selected (or locked) OHLCV field."""

_SHAPED_CALCULATORS: dict[
    IndicatorKind,
    Callable[[IndicatorDefinition, Sequence[Candle]], tuple[Decimal | None, ...]],
] = {
    IndicatorKind.KAMA: _kaufman_adaptive_values,
    IndicatorKind.PARABOLIC_SAR: _parabolic_sar_values,
    IndicatorKind.ULTIMATE_OSCILLATOR: _ultimate_oscillator_values,
    IndicatorKind.AWESOME_OSCILLATOR: _awesome_oscillator_values,
    IndicatorKind.BOLLINGER_PERCENT_B: _bollinger_percent_b_values,
    IndicatorKind.BOLLINGER_BANDWIDTH: _bollinger_bandwidth_values,
    IndicatorKind.HISTORICAL_VOLATILITY: _historical_volatility_values,
}
"""Single-output kinds whose parameters are not the plain ``{period}`` block."""

_MULTI_SERIES_CALCULATORS: dict[
    IndicatorKind,
    Callable[[IndicatorDefinition, Sequence[Candle]], dict[str, tuple[Decimal | None, ...]]],
] = {
    IndicatorKind.MACD: _macd_series,
    IndicatorKind.BOLLINGER: _bollinger_series,
    IndicatorKind.STOCHASTIC: _stochastic_series,
    IndicatorKind.ADX: _adx_series,
    IndicatorKind.SUPERTREND: _supertrend_series,
    IndicatorKind.AROON: _aroon_series,
    IndicatorKind.ICHIMOKU: _ichimoku_series,
    IndicatorKind.VORTEX: _vortex_series,
    IndicatorKind.LINEAR_REGRESSION: _linear_regression_series,
    IndicatorKind.STOCHASTIC_RSI: _stochastic_rsi_series,
    IndicatorKind.PPO: _ppo_series,
    IndicatorKind.TSI: _true_strength_series,
    IndicatorKind.KELTNER: _keltner_series,
    IndicatorKind.DONCHIAN: _donchian_series,
    IndicatorKind.OBV: _on_balance_volume_series,
    IndicatorKind.ACCUMULATION_DISTRIBUTION: _accumulation_distribution_series,
}
"""Multi-series kinds; output names come from ``indicator_output_series``."""
