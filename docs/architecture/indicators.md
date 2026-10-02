# Indicator seed, warmup, and first-valid conventions

Deterministic indicators live in `thytrader.research.indicators` and are shared by backtest signal
traces, paper/live `evaluate_latest_entry`, and operator diagnostics.

## Warmup and history

- **Research/backtest:** `evaluate_signal_trace` selects contiguous candles from
  `warmup.starts_at` through `evaluation.ends_at` (exclusive). Indicators evolve across the full
  prefix; signal records begin at `evaluation.starts_at`.
- **Paper/live:** the execution worker fetches from
  `warmup_starts_at(entry_bar_bucket(deploy_created_at), warmup_bars, timeframe)` through the
  closed bar under evaluation. Catch-up replays use the same anchor with `as_of` set to each due
  bar so indicator state is not truncated to a sliding window anchored only to wall-clock now.
- **Strategy `data_requirements.warmup_bars`:** must be ≥ per-indicator minimum periods declared on
  the strategy document. It is a coverage floor, not a guarantee that a short sliding window
  matches infinite-prefix research semantics.

## First valid index

| Kind | First defined value |
| --- | --- |
| SMA, volume SMA, highest, lowest, stdev, WMA, VWMA, linear regression, Donchian, z-score, CMF, rolling VWAP, Bollinger %B and bandwidth, NATR, choppiness, Supertrend | After `period` completed bars (index `period - 1`) |
| ROC, momentum, Aroon, Vortex, CMO, force index, percent rank, historical volatility, KAMA | After `period + 1` bars (they read `period` previous-bar comparisons) |
| EMA | After `period` bars; seeded by SMA of the first `period` values |
| DEMA, TEMA, TRIX | After `2 * period - 1`, `3 * period - 2`, and `3 * period - 1` bars (chained SMA-seeded EMAs) |
| Hull MA | After `period + floor(sqrt(period)) - 1` bars |
| RSI, ATR, ADX, MFI, Williams %R, CCI | After `period` completed changes/bars per Wilder/recipe in code |
| MACD, PPO, Bollinger, Stochastic, Stochastic RSI, TSI, Keltner, Ichimoku, Ultimate and Awesome oscillators | After each nested period; the warmup column of the operator `indicators` report gives the formula |
| OBV, accumulation/distribution | Line on every bar; `signal` after `signal_period` bars |
| Parabolic SAR | From bar 1 (bar 0 seeds the trend) |
| Identity, constant | Every bar |

The warmup (`indicator_min_warmup`) is the bar count at which **every** output series of the
declaration is defined. Operands referencing `None` fail closed to `UNDEFINED` entry outcomes. The
operator `indicators` report lists each kind's `warmup` formula and `default_warmup_bars`
([ADR 0086](../decisions/0086-indicator-catalog-expansion-and-offset.md)).

## Bar lag (`offset`)

An optional `offset` (0–500, any kind except `constant`) shifts every output series of one
declaration by that many completed bars of the indicator's own clock: bar `t` reports the value
from bar `t - offset`, and the first `offset` bars are undefined. Warmup adds `offset`. The shift
happens inside `calculate_indicator_rows`, so research traces, paper/live entries, ATR stops, HTF
filters, and extra-TF overlays all read the same lagged values. `offset: 0` and an omitted offset
are identical documents.

## Zero divisors

Where a formula divides by a range, a sum, or a deviation that can be zero, the value is undefined
(fail closed) unless the formula documents a convention. Conventions: RSI 50/100, KAMA efficiency
1 on a flat window, A/D and CMF multiplier 0 on a zero-range bar, Aroon ties toward the most recent
bar. Undefined cases include MFI, Williams %R, CCI, stochastic, Vortex, stochastic RSI, the
Ultimate Oscillator, CMO, TSI, %B, choppiness, z-score, and zero-volume VWMA/CMF/VWAP windows. See
[signal evaluation](signal-evaluation.md) for each formula.

## Empty input shape

When zero candles or zero source values are supplied, each indicator returns a tuple with **length
zero**, not a placeholder `None` row. Multi-series indicators return empty series tuples per key.
This keeps `calculate_indicator_rows` row count aligned with candle count.

## Serialization

`canonical_decimal` normalizes finite engine `Decimal` values for traces, results, and dataset
**numeric** fingerprints (schema v2). It does not change live calculation semantics.
