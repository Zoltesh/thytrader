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

from thytrader.strategies.models import (
    BollingerIndicatorParameters,
    ConstantIndicatorParameters,
    IndicatorKind,
    IndicatorParameters,
    MacdIndicatorParameters,
    StochasticIndicatorParameters,
    indicator_output_series,
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
    """Return one mapping of output keys to values for every supplied candle."""
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
    if indicator.kind is IndicatorKind.MACD:
        return _macd_series(indicator, candles)
    if indicator.kind is IndicatorKind.BOLLINGER:
        return _bollinger_series(indicator, candles)
    if indicator.kind is IndicatorKind.STOCHASTIC:
        return _stochastic_series(indicator, candles)
    if indicator.kind is IndicatorKind.ADX:
        return _adx_series(indicator, candles)
    raise IndicatorCalculationError(
        f"Indicator kind {indicator.kind.value} is not implemented by this engine contract."
    )


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
    parameters = indicator.parameters
    if not isinstance(parameters, IndicatorParameters):
        raise IndicatorCalculationError(
            f"Indicator kind {indicator.kind.value} is not implemented by this engine contract."
        )
    period = parameters.period
    if indicator.kind is IndicatorKind.MFI:
        return _money_flow_index(candles, period)
    hlc_calculator = _HLC_CALCULATORS.get(indicator.kind)
    if hlc_calculator is not None:
        return hlc_calculator(candles, period)
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


_HLC_CALCULATORS: dict[
    IndicatorKind,
    Callable[[Sequence[Candle], int], tuple[Decimal | None, ...]],
] = {
    IndicatorKind.ATR: _average_true_range,
    IndicatorKind.WILLIAMS_R: _williams_percent_r,
    IndicatorKind.CCI: _commodity_channel_index,
}
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
}
