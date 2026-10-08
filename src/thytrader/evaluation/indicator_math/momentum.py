"""Momentum oscillators (MACD, RSI, stochastics, ROC, CCI, PPO, TSI, TRIX, and more)."""

from __future__ import annotations

from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING

from thytrader.evaluation.indicator_math.core import (
    IndicatorCalculationError,
    _difference,
    _locked_source_series,
    _parameters_of,
)
from thytrader.evaluation.indicator_math.moving_averages import (
    _exponential_moving_average,
    _exponential_moving_average_optional,
    _simple_moving_average,
    _simple_moving_average_defined,
)
from thytrader.evaluation.indicator_math.rolling_statistics import (
    _rolling_extreme,
    _rolling_extreme_optional,
)
from thytrader.strategies.models import (
    AwesomeOscillatorIndicatorParameters,
    MacdIndicatorParameters,
    StochasticIndicatorParameters,
    StochasticRsiIndicatorParameters,
    TsiIndicatorParameters,
    UltimateOscillatorIndicatorParameters,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import IndicatorDefinition


_FLAT_RSI_RANGE = Decimal("1e-30")
"""Stochastic RSI treats an RSI range at or below this as zero (64-digit rounding noise)."""


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
