"""Independent Decimal reference implementations for the wider indicator catalog.

These are deliberately written apart from ``thytrader.research.indicators``: each value is
recomputed from scratch per bar straight from the documented formula, so a regression
in a shared engine helper cannot silently move both sides. The deterministic candle
generator uses integer LCG arithmetic only, so it is identical on every Python version.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from itertools import pairwise
from math import isqrt
from typing import TYPE_CHECKING

from thytrader.market_data.models import Candle

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

ENGINE_CONTEXT = Context(prec=64, rounding=ROUND_HALF_EVEN, Emin=-6143, Emax=6144)
TOLERANCE = Decimal("1e-40")
FLAT_RSI_RANGE = Decimal("1e-30")
"""Documented Stochastic RSI zero-range threshold (64-digit Wilder rounding noise)."""
_TICK = Decimal("0.0001")
_LCG_MULTIPLIER = 6_364_136_223_846_793_005
_LCG_INCREMENT = 1_442_695_040_888_963_407
_LCG_MODULUS = 2**64

Series = list[Decimal | None]


def synthetic_candles(count: int, *, seed: int = 20_261_002) -> tuple[Candle, ...]:
    """Return a deterministic random-walk OHLCV series with valid candle geometry."""
    state = seed

    def unit() -> int:
        """Return the next pseudo-random integer in [0, 10_000)."""
        nonlocal state
        state = (state * _LCG_MULTIPLIER + _LCG_INCREMENT) % _LCG_MODULUS
        return (state >> 33) % 10_000

    start = datetime(2026, 1, 1, tzinfo=UTC)
    close = Decimal(100)
    candles: list[Candle] = []
    for index in range(count):
        opened = close
        move = Decimal(unit() - 5_000) / Decimal(250_000)
        close = (opened * (1 + move)).quantize(_TICK)
        upper_wick = Decimal(unit()) / Decimal(2_000_000)
        lower_wick = Decimal(unit()) / Decimal(2_000_000)
        high = max((max(opened, close) * (1 + upper_wick)).quantize(_TICK), opened, close)
        low = min((min(opened, close) * (1 - lower_wick)).quantize(_TICK), opened, close)
        volume = Decimal(unit() + 1) / Decimal(10)
        candles.append(Candle(start + timedelta(hours=index), opened, high, low, close, volume))
    return tuple(candles)


def flat_candles(count: int, *, price: str = "50", volume: str = "5") -> tuple[Candle, ...]:
    """Return ``count`` identical bars (zero ranges and zero changes everywhere)."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    value = Decimal(price)
    return tuple(
        Candle(start + timedelta(hours=index), value, value, value, value, Decimal(volume))
        for index in range(count)
    )


def close_enough(actual: Decimal | None, expected: Decimal | None) -> bool:
    """Return True when both are undefined or agree to 1e-40 relative (64-digit math)."""
    if actual is None or expected is None:
        return actual is None and expected is None
    scale = max(Decimal(1), abs(expected))
    return abs(actual - expected) <= TOLERANCE * scale


def _sum(values: Sequence[Decimal]) -> Decimal:
    """Oldest-first sum starting at exact zero."""
    total = Decimal(0)
    for value in values:
        total += value
    return total


def _window(values: Sequence[Decimal | None], index: int, period: int) -> list[Decimal] | None:
    """Return the complete, fully defined window ending at ``index``, else None."""
    if index + 1 < period:
        return None
    window = list(values[index + 1 - period : index + 1])
    if any(value is None for value in window):
        return None
    return [value for value in window if value is not None]


def ref_sma(values: Sequence[Decimal | None], period: int) -> Series:
    """Arithmetic mean of each complete defined window."""
    result: Series = []
    for index in range(len(values)):
        window = _window(values, index, period)
        result.append(None if window is None else _sum(window) / Decimal(period))
    return result


def ref_ema(values: Sequence[Decimal | None], period: int) -> Series:
    """SMA-seeded EMA ((p - 1) * prev + 2 * x) / (p + 1); holes pass through untouched."""
    result: Series = []
    seed: list[Decimal] = []
    previous: Decimal | None = None
    for value in values:
        if value is None:
            result.append(None)
        elif previous is None:
            seed.append(value)
            if len(seed) == period:
                previous = _sum(seed) / Decimal(period)
            result.append(previous)
        else:
            previous = (Decimal(period - 1) * previous + Decimal(2) * value) / Decimal(period + 1)
            result.append(previous)
    return result


def ref_wma(values: Sequence[Decimal | None], period: int) -> Series:
    """Linear WMA with weights 1 (oldest) .. period (newest)."""
    result: Series = []
    for index in range(len(values)):
        window = _window(values, index, period)
        if window is None:
            result.append(None)
            continue
        weighted = _sum([value * Decimal(weight) for weight, value in enumerate(window, 1)])
        result.append(weighted / _sum([Decimal(weight) for weight in range(1, period + 1)]))
    return result


def ref_true_ranges(candles: Sequence[Candle]) -> list[Decimal]:
    """True range with ``high - low`` on the first bar."""
    ranges: list[Decimal] = []
    for index, candle in enumerate(candles):
        if index == 0:
            ranges.append(candle.high - candle.low)
            continue
        prior = candles[index - 1].close
        ranges.append(
            max(candle.high - candle.low, abs(candle.high - prior), abs(candle.low - prior))
        )
    return ranges


def ref_atr(candles: Sequence[Candle], period: int) -> Series:
    """Wilder ATR seeded with the mean of the first ``period`` true ranges."""
    ranges = ref_true_ranges(candles)
    result: Series = []
    previous: Decimal | None = None
    for index, value in enumerate(ranges):
        if index + 1 < period:
            result.append(None)
            continue
        if previous is None:
            previous = _sum(ranges[:period]) / Decimal(period)
        else:
            previous = (previous * Decimal(period - 1) + value) / Decimal(period)
        result.append(previous)
    return result


def ref_rsi(closes: Sequence[Decimal], period: int) -> Series:
    """Wilder RSI after ``period`` changes (both averages zero -> 50, no losses -> 100)."""
    result: Series = [None] if closes else []
    gains: list[Decimal] = []
    losses: list[Decimal] = []
    average_gain = average_loss = Decimal(0)
    for index in range(1, len(closes)):
        change = closes[index] - closes[index - 1]
        gains.append(max(change, Decimal(0)))
        losses.append(max(-change, Decimal(0)))
        if index < period:
            result.append(None)
            continue
        if index == period:
            average_gain = _sum(gains) / Decimal(period)
            average_loss = _sum(losses) / Decimal(period)
        else:
            average_gain = (average_gain * Decimal(period - 1) + gains[-1]) / Decimal(period)
            average_loss = (average_loss * Decimal(period - 1) + losses[-1]) / Decimal(period)
        if average_gain == 0 and average_loss == 0:
            result.append(Decimal(50))
        elif average_loss == 0:
            result.append(Decimal(100))
        else:
            result.append(Decimal(100) * average_gain / (average_gain + average_loss))
    return result


def _pairs(left: Series, right: Series, combine: Callable[[Decimal, Decimal], Decimal]) -> Series:
    """Combine two aligned series, undefined when either side is undefined."""
    return [
        None if first is None or second is None else combine(first, second)
        for first, second in zip(left, right, strict=True)
    ]


def ref_dema(values: Sequence[Decimal], period: int) -> Series:
    """2 * EMA - EMA(EMA)."""
    first = ref_ema(values, period)
    return _pairs(first, ref_ema(first, period), lambda one, two: Decimal(2) * one - two)


def ref_tema(values: Sequence[Decimal], period: int) -> Series:
    """(3 * E1 - 3 * E2) + E3."""
    first = ref_ema(values, period)
    second = ref_ema(first, period)
    third = ref_ema(second, period)
    return [
        None if a is None or b is None or c is None else Decimal(3) * a - Decimal(3) * b + c
        for a, b, c in zip(first, second, third, strict=True)
    ]


def ref_hma(values: Sequence[Decimal], period: int) -> Series:
    """WMA(2 * WMA(n // 2) - WMA(n), isqrt(n))."""
    raw = _pairs(
        ref_wma(values, period // 2),
        ref_wma(values, period),
        lambda short, long: Decimal(2) * short - long,
    )
    return ref_wma(raw, isqrt(period))


def ref_kama(values: Sequence[Decimal], period: int, fast: int, slow: int) -> Series:
    """Kaufman AMA seeded at bar period - 1; a flat window has efficiency 1."""
    result: Series = [None] * min(period, len(values))
    if len(values) <= period:
        return result
    fast_sc = Decimal(2) / Decimal(fast + 1)
    slow_sc = Decimal(2) / Decimal(slow + 1)
    kama = values[period - 1]
    for index in range(period, len(values)):
        change = abs(values[index] - values[index - period])
        noise = _sum([abs(values[j] - values[j - 1]) for j in range(index - period + 1, index + 1)])
        efficiency = Decimal(1) if noise == 0 else change / noise
        base = efficiency * (fast_sc - slow_sc) + slow_sc
        kama = kama + base * base * (values[index] - kama)
        result.append(kama)
    return result


def ref_vwma(candles: Sequence[Candle], period: int) -> Series:
    """Sum(close * volume) / sum(volume)."""
    result: Series = []
    for index in range(len(candles)):
        if index + 1 < period:
            result.append(None)
            continue
        window = candles[index + 1 - period : index + 1]
        volume = _sum([candle.volume for candle in window])
        weighted = _sum([candle.close * candle.volume for candle in window])
        result.append(None if volume == 0 else weighted / volume)
    return result


def ref_supertrend(
    candles: Sequence[Candle], period: int, multiplier: Decimal
) -> tuple[Series, Series]:
    """TradingView ta.supertrend recurrence with direction 1 = up, -1 = down."""
    atr = ref_atr(candles, period)
    values: Series = []
    directions: Series = []
    upper = lower = Decimal(0)
    direction = 0
    for index, candle in enumerate(candles):
        atr_value = atr[index]
        if atr_value is None:
            values.append(None)
            directions.append(None)
            continue
        width = multiplier * atr_value
        mid = (candle.high + candle.low) / Decimal(2)
        basic_upper, basic_lower = mid + width, mid - width
        if direction == 0:
            upper, lower, direction = basic_upper, basic_lower, -1
        else:
            prior_close = candles[index - 1].close
            upper = basic_upper if basic_upper < upper or prior_close > upper else upper
            lower = basic_lower if basic_lower > lower or prior_close < lower else lower
            if direction == -1:
                direction = 1 if candle.close > upper else -1
            else:
                direction = -1 if candle.close < lower else 1
        values.append(lower if direction == 1 else upper)
        directions.append(Decimal(direction))
    return values, directions


def ref_parabolic_sar(candles: Sequence[Candle], step: Decimal, cap: Decimal) -> Series:
    """Port of TA-Lib TA_SAR (initial trend from bar 1's minus-DM)."""
    if len(candles) < 2:
        return [None] * len(candles)
    down = candles[0].low - candles[1].low
    up = candles[1].high - candles[0].high
    is_long = not (down > 0 and up < down)
    extreme = candles[1].high if is_long else candles[1].low
    sar = candles[0].low if is_long else candles[0].high
    acceleration = step
    new_high, new_low = candles[1].high, candles[1].low
    result: Series = [None]
    for today in range(1, len(candles)):
        prev_high, prev_low = new_high, new_low
        new_high, new_low = candles[today].high, candles[today].low
        if is_long and new_low <= sar:
            is_long = False
            sar = max(extreme, prev_high, new_high)
            result.append(sar)
            acceleration, extreme = step, new_low
            sar = max(sar + acceleration * (extreme - sar), prev_high, new_high)
        elif is_long:
            result.append(sar)
            if new_high > extreme:
                extreme = new_high
                acceleration = min(acceleration + step, cap)
            sar = min(sar + acceleration * (extreme - sar), prev_low, new_low)
        elif new_high >= sar:
            is_long = True
            sar = min(extreme, prev_low, new_low)
            result.append(sar)
            acceleration, extreme = step, new_high
            sar = min(sar + acceleration * (extreme - sar), prev_low, new_low)
        else:
            result.append(sar)
            if new_low < extreme:
                extreme = new_low
                acceleration = min(acceleration + step, cap)
            sar = max(sar + acceleration * (extreme - sar), prev_high, new_high)
    return result


def ref_aroon(candles: Sequence[Candle], period: int) -> tuple[Series, Series, Series]:
    """Aroon over period + 1 bars; ties resolve to the most recent bar."""
    up: Series = []
    down: Series = []
    oscillator: Series = []
    for index in range(len(candles)):
        if index < period:
            up.append(None)
            down.append(None)
            oscillator.append(None)
            continue
        window = list(range(index - period, index + 1))
        best_high = max(window, key=lambda j: (candles[j].high, j))
        best_low = min(window, key=lambda j: (candles[j].low, -j))
        up_value = Decimal(100) * Decimal(period - (index - best_high)) / Decimal(period)
        down_value = Decimal(100) * Decimal(period - (index - best_low)) / Decimal(period)
        up.append(up_value)
        down.append(down_value)
        oscillator.append(up_value - down_value)
    return up, down, oscillator


def ref_midpoint(candles: Sequence[Candle], period: int) -> Series:
    """(max high + min low) / 2 over each complete window."""
    result: Series = []
    for index in range(len(candles)):
        if index + 1 < period:
            result.append(None)
            continue
        window = candles[index + 1 - period : index + 1]
        top = max(candle.high for candle in window)
        bottom = min(candle.low for candle in window)
        result.append((top + bottom) / Decimal(2))
    return result


def ref_vortex(candles: Sequence[Candle], period: int) -> tuple[Series, Series]:
    """VI+ and VI- over the last ``period`` bars with a previous bar."""
    ranges = ref_true_ranges(candles)
    plus: Series = []
    minus: Series = []
    for index in range(len(candles)):
        if index < period:
            plus.append(None)
            minus.append(None)
            continue
        positions = range(index - period + 1, index + 1)
        total = _sum([ranges[j] for j in positions])
        if total == 0:
            plus.append(None)
            minus.append(None)
            continue
        plus.append(_sum([abs(candles[j].high - candles[j - 1].low) for j in positions]) / total)
        minus.append(_sum([abs(candles[j].low - candles[j - 1].high) for j in positions]) / total)
    return plus, minus


def ref_linear_regression(values: Sequence[Decimal], period: int) -> tuple[Series, Series]:
    """Least-squares endpoint and slope with x = 0 (oldest) .. period - 1."""
    fitted: Series = []
    slopes: Series = []
    n = Decimal(period)
    sum_x = Decimal(period * (period - 1) // 2)
    sum_xx = Decimal((period - 1) * period * (2 * period - 1) // 6)
    for index in range(len(values)):
        window = _window(values, index, period)
        if window is None:
            fitted.append(None)
            slopes.append(None)
            continue
        sum_y = _sum(window)
        sum_xy = _sum([Decimal(x) * y for x, y in enumerate(window)])
        slope = (n * sum_xy - sum_x * sum_y) / (n * sum_xx - sum_x * sum_x)
        intercept = (sum_y - slope * sum_x) / n
        fitted.append(intercept + slope * Decimal(period - 1))
        slopes.append(slope)
    return fitted, slopes


def ref_trix(values: Sequence[Decimal], period: int) -> Series:
    """100 * (E3 - E3[1]) / E3[1] for the triple SMA-seeded EMA."""
    third = ref_ema(ref_ema(ref_ema(values, period), period), period)
    result: Series = [None] if third else []
    for index in range(1, len(third)):
        current, previous = third[index], third[index - 1]
        if current is None or previous is None or previous == 0:
            result.append(None)
            continue
        result.append(Decimal(100) * (current - previous) / previous)
    return result


def ref_stochastic_rsi(
    closes: Sequence[Decimal], rsi_period: int, stoch_period: int, k_period: int, d_period: int
) -> tuple[Series, Series]:
    """Stochastic of RSI with SMA %K and %D (zero RSI range is undefined)."""
    rsi = ref_rsi(closes, rsi_period)
    raw: Series = []
    for index in range(len(rsi)):
        window = _window(rsi, index, stoch_period)
        value = rsi[index]
        if window is None or value is None or max(window) - min(window) <= FLAT_RSI_RANGE:
            raw.append(None)
            continue
        raw.append(Decimal(100) * (value - min(window)) / (max(window) - min(window)))
    percent_k = ref_sma(raw, k_period)
    return percent_k, ref_sma(percent_k, d_period)


def ref_ppo(closes: Sequence[Decimal], fast: int, slow: int, signal: int) -> tuple[Series, ...]:
    """PPO line, its EMA signal, and the histogram."""
    line = _pairs(
        ref_ema(closes, fast),
        ref_ema(closes, slow),
        lambda f, s: Decimal(100) * (f - s) / s,
    )
    signal_line = ref_ema(line, signal)
    return line, signal_line, _pairs(line, signal_line, lambda a, b: a - b)


def ref_ultimate_oscillator(
    candles: Sequence[Candle], short: int, medium: int, long: int
) -> Series:
    """Williams UO; any zero true-range window is undefined."""
    result: Series = []
    for index in range(len(candles)):
        if index < long:
            result.append(None)
            continue
        averages: list[Decimal | None] = []
        for period in (short, medium, long):
            pressure = Decimal(0)
            ranges = Decimal(0)
            for j in range(index - period + 1, index + 1):
                low = min(candles[j].low, candles[j - 1].close)
                pressure += candles[j].close - low
                ranges += max(candles[j].high, candles[j - 1].close) - low
            averages.append(None if ranges == 0 else pressure / ranges)
        if any(value is None for value in averages):
            result.append(None)
            continue
        first, second, third = (value for value in averages if value is not None)
        result.append(
            Decimal(100) * (Decimal(4) * first + Decimal(2) * second + third) / Decimal(7)
        )
    return result


def ref_awesome_oscillator(candles: Sequence[Candle], fast: int, slow: int) -> Series:
    """SMA(fast) - SMA(slow) of (high + low) / 2."""
    median: Series = [(candle.high + candle.low) / Decimal(2) for candle in candles]
    return _pairs(ref_sma(median, fast), ref_sma(median, slow), lambda a, b: a - b)


def ref_cmo(closes: Sequence[Decimal], period: int) -> Series:
    """Chande momentum oscillator from plain sums of gains and losses."""
    result: Series = []
    for index in range(len(closes)):
        if index < period:
            result.append(None)
            continue
        changes = [closes[j] - closes[j - 1] for j in range(index - period + 1, index + 1)]
        gains = _sum([change for change in changes if change > 0])
        losses = _sum([-change for change in changes if change < 0])
        total = gains + losses
        result.append(None if total == 0 else Decimal(100) * (gains - losses) / total)
    return result


def ref_tsi(closes: Sequence[Decimal], long: int, short: int, signal: int) -> tuple[Series, Series]:
    """True strength index and its EMA signal."""
    changes: Series = [None] + [b - a for a, b in pairwise(closes)]
    magnitudes: Series = [None if c is None else abs(c) for c in changes]
    top = ref_ema(ref_ema(changes, long), short)
    bottom = ref_ema(ref_ema(magnitudes, long), short)
    tsi: Series = [
        None if t is None or b is None or b == 0 else Decimal(100) * t / b
        for t, b in zip(top, bottom, strict=True)
    ]
    return tsi, ref_ema(tsi, signal)


def ref_keltner(
    candles: Sequence[Candle], period: int, atr_period: int, multiplier: Decimal
) -> tuple[Series, Series, Series]:
    """EMA(close) middle with ATR-multiple bands."""
    middle = ref_ema([candle.close for candle in candles], period)
    atr = ref_atr(candles, atr_period)
    upper = _pairs(middle, atr, lambda m, a: m + multiplier * a)
    lower = _pairs(middle, atr, lambda m, a: m - multiplier * a)
    return upper, middle, lower


def ref_bollinger(
    closes: Sequence[Decimal], period: int, multiplier: Decimal
) -> tuple[Series, ...]:
    """SMA middle and population-stdev bands (zero variance -> zero width)."""
    middle: Series = []
    upper: Series = []
    lower: Series = []
    for index in range(len(closes)):
        window = _window(closes, index, period)
        if window is None:
            middle.append(None)
            upper.append(None)
            lower.append(None)
            continue
        mean = _sum(window) / Decimal(period)
        variance = _sum([(value - mean) * (value - mean) for value in window]) / Decimal(period)
        deviation = Decimal(0) if variance <= 0 else variance.sqrt()
        middle.append(mean)
        upper.append(mean + multiplier * deviation)
        lower.append(mean - multiplier * deviation)
    return middle, upper, lower


def ref_natr(candles: Sequence[Candle], period: int) -> Series:
    """100 * ATR / close."""
    return [
        None if atr is None else Decimal(100) * atr / candle.close
        for candle, atr in zip(candles, ref_atr(candles, period), strict=True)
    ]


def ref_choppiness(candles: Sequence[Candle], period: int) -> Series:
    """100 * log10(sum TR / (HH - LL)) / log10(period)."""
    ranges = ref_true_ranges(candles)
    result: Series = []
    for index in range(len(candles)):
        if index + 1 < period:
            result.append(None)
            continue
        window = candles[index + 1 - period : index + 1]
        span = max(candle.high for candle in window) - min(candle.low for candle in window)
        if span == 0:
            result.append(None)
            continue
        total = _sum(ranges[index + 1 - period : index + 1])
        result.append(Decimal(100) * (total / span).log10() / Decimal(period).log10())
    return result


def ref_historical_volatility(
    closes: Sequence[Decimal], period: int, annualization: int | None
) -> Series:
    """100 * sample stdev of ln(close / prior close), optionally times sqrt(periods)."""
    returns: Series = [None] + [(b / a).ln() for a, b in pairwise(closes)]
    result: Series = []
    for index in range(len(closes)):
        window = _window(returns, index, period)
        if window is None:
            result.append(None)
            continue
        mean = _sum(window) / Decimal(period)
        variance = _sum([(value - mean) * (value - mean) for value in window]) / Decimal(period - 1)
        value = Decimal(100) * (Decimal(0) if variance <= 0 else variance.sqrt())
        result.append(value if annualization is None else value * Decimal(annualization).sqrt())
    return result


def ref_obv(candles: Sequence[Candle]) -> Series:
    """OBV starting at 0 on the first bar."""
    result: Series = []
    running = Decimal(0)
    for index, candle in enumerate(candles):
        if index and candle.close > candles[index - 1].close:
            running += candle.volume
        elif index and candle.close < candles[index - 1].close:
            running -= candle.volume
        result.append(running)
    return result


def _multiplier(candle: Candle) -> Decimal:
    """Money-flow multiplier; zero range -> 0."""
    span = candle.high - candle.low
    if span == 0:
        return Decimal(0)
    return ((candle.close - candle.low) - (candle.high - candle.close)) / span


def ref_accumulation_distribution(candles: Sequence[Candle]) -> Series:
    """Cumulative multiplier * volume."""
    result: Series = []
    running = Decimal(0)
    for candle in candles:
        running += _multiplier(candle) * candle.volume
        result.append(running)
    return result


def ref_cmf(candles: Sequence[Candle], period: int) -> Series:
    """Sum(multiplier * volume) / sum(volume)."""
    result: Series = []
    for index in range(len(candles)):
        if index + 1 < period:
            result.append(None)
            continue
        window = candles[index + 1 - period : index + 1]
        volume = _sum([candle.volume for candle in window])
        flow = _sum([_multiplier(candle) * candle.volume for candle in window])
        result.append(None if volume == 0 else flow / volume)
    return result


def ref_vwap(candles: Sequence[Candle], period: int) -> Series:
    """Sum(typical * volume) / sum(volume), typical = (h + l + c) / 3."""
    result: Series = []
    for index in range(len(candles)):
        if index + 1 < period:
            result.append(None)
            continue
        window = candles[index + 1 - period : index + 1]
        volume = _sum([candle.volume for candle in window])
        weighted = _sum([(c.high + c.low + c.close) / Decimal(3) * c.volume for c in window])
        result.append(None if volume == 0 else weighted / volume)
    return result


def ref_force_index(candles: Sequence[Candle], period: int) -> Series:
    """EMA of (close - prior close) * volume."""
    raw: Series = [None] + [(b.close - a.close) * b.volume for a, b in pairwise(candles)]
    return ref_ema(raw, period)


def ref_zscore(values: Sequence[Decimal], period: int) -> Series:
    """(x - SMA) / population stdev; zero stdev is undefined."""
    middle, upper, _lower = ref_bollinger(values, period, Decimal(1))
    return [
        None if m is None or u is None or u == m else (value - m) / (u - m)
        for value, m, u in zip(values, middle, upper, strict=True)
    ]


def ref_percent_rank(values: Sequence[Decimal], period: int) -> Series:
    """Percent of the previous ``period`` values at or below the current one."""
    return [
        None
        if index < period
        else Decimal(100)
        * Decimal(sum(1 for j in range(index - period, index) if values[j] <= values[index]))
        / Decimal(period)
        for index in range(len(values))
    ]


def in_engine_context[ResultT](function: Callable[[], ResultT]) -> ResultT:
    """Evaluate one reference computation under the engine's 64-digit context."""
    with localcontext(ENGINE_CONTEXT):
        return function()
