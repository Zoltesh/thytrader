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
| SMA, volume SMA, highest, lowest, stdev, ROC, momentum, WMA | After `period` completed bars (index `period - 1`) |
| EMA | After `period` bars; seeded by SMA of the first `period` values |
| RSI, ATR, ADX, MFI, Williams %R, CCI | After `period` completed changes/bars per Wilder/recipe in code |
| MACD, Bollinger, Stochastic | After each nested period in the implementation |
| Identity, constant | Every bar |

Operands referencing `None` fail closed to `UNDEFINED` entry outcomes.

## Empty input shape

When zero candles or zero source values are supplied, each indicator returns a tuple with **length
zero**, not a placeholder `None` row. Multi-series indicators return empty series tuples per key.
This keeps `calculate_indicator_rows` row count aligned with candle count.

## Serialization

`canonical_decimal` normalizes finite engine `Decimal` values for traces, results, and dataset
**numeric** fingerprints (schema v2). It does not change live calculation semantics.
