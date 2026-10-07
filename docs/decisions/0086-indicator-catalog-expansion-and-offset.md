# 0086: Wider indicator catalog, registry-rendered builder catalog, and indicator bar lag

- Status: Accepted — operand-level-lag deferral superseded by [0099](0099-operand-level-indicator-offsets.md)
- Date: 2026-10-02
- Extends: [0005](0005-canonical-strategy-schema.md) (two input tuples and the optional
  `offset` declaration field), [0008](0008-deterministic-signal-evaluation.md) (new formula
  contracts), and [0047](0047-wider-fail-closed-indicator-catalog.md) (catalog)
- Relates to: [0025](0025-multi-timeframe-htf-filter.md), [0026](0026-phase-9-single-output-indicator-catalog.md)–[0029](0029-phase-9-wma-momentum-mfi.md),
  [0032](0032-phase-9-macd-bollinger.md), [0042](0042-per-indicator-timeframes.md),
  [0044](0044-parameter-sweeps-wfo-stitched-equity.md), [0052](0052-richer-sweep-axes-study-catalog.md),
  [0082](0082-strategy-root-mutable-strategies-auto-snapshots.md), [0083](0083-unified-backtest-model.md)

## Context

Operators reported that the 21-kind catalog was too thin to build real strategies: no adaptive
or low-lag averages, no Supertrend/SAR trailing trends, no channels besides Bollinger, few volume
studies, and no way to read a value from an earlier bar. Every window includes the current
completed bar, so a classic breakout (`close > highest(high, 20)` of the *previous* 20 bars) was
not expressible at all. The browser builder also re-declared the catalog by hand in TypeScript
(kind lists, input locks, period caps, warmup formulas) and labelled sizing "Minimum USD
notional" even for USDC strategies.

The catalog rules from ADR 0026–0047 still apply: no TA-library passthrough, Decimal arithmetic
in the `decimal64-half-even-v1` context, oldest-first left folds, undefined (never `0`) while
warming up, fail-closed validation, one shared calculator for research, paper, and live, and
existing documents and fingerprints must not change.

## Decision

### Thirty-two new kinds

All are in the same LTF and `htf_filter` registry, accept the optional per-indicator `timeframe`
(ADR 0042) and the new `offset`, and are sweepable. Parameters are required in documents;
builder defaults are in parentheses. Bounds marked "effective" are the field bounds tightened by
an ordering rule (for example `fast_period < slow_period`), so every advertised bound is
reachable.

| Kind | Input | Parameters (default) | Outputs | Warmup bars |
|---|---|---|---|---|
| `dema` | one OHLCV field | `period` 2–500 (20) | single | `2 * period - 1` |
| `tema` | one OHLCV field | `period` 2–500 (20) | single | `3 * period - 2` |
| `hma` | one OHLCV field | `period` 2–500 (20) | single | `period + floor(sqrt(period)) - 1` |
| `kama` | one OHLCV field | `period` 2–100 (10), `fast_period` 2–100 (2), `slow_period` 3–500 (30); fast < slow | single | `period + 1` |
| `vwma` | `close, volume` | `period` 2–500 (20) | single | `period` |
| `supertrend` | `high, low, close` | `period` 2–100 (10), `multiplier` (0, 10] (`3`) | `value`, `direction` | `period` |
| `parabolic_sar` | `high, low` | `step` (0, 1] (`0.02`), `max_step` (0, 1] (`0.2`); step ≤ max_step | single | `2` |
| `aroon` | `high, low` | `period` 2–500 (25) | `up`, `down`, `oscillator` | `period + 1` |
| `ichimoku` | `high, low` | `tenkan_period` 2–498 (9), `kijun_period` 3–499 (26), `senkou_b_period` 4–500 (52); tenkan < kijun < senkou_b | `tenkan`, `kijun`, `senkou_a`, `senkou_b` | max of the three |
| `vortex` | `high, low, close` | `period` 2–500 (14) | `plus`, `minus` | `period + 1` |
| `linear_regression` | one OHLCV field | `period` 2–500 (20) | `value`, `slope` | `period` |
| `trix` | `close` | `period` 2–500 (15) | single | `3 * period - 1` |
| `stochastic_rsi` | `close` | `rsi_period` 2–100 (14), `stoch_period` 2–100 (14), `k_period` 1–100 (3), `d_period` 1–100 (3) | `k`, `d` | `rsi + stoch + k + d - 2` |
| `ppo` | `close` | `fast_period` 2–499 (12), `slow_period` 3–500 (26), `signal_period` 2–500 (9) | `ppo`, `signal`, `histogram` | `slow + signal - 1` |
| `ultimate_oscillator` | `high, low, close` | `short_period` 2–98 (7), `medium_period` 3–99 (14), `long_period` 4–100 (28); short < medium < long | single | `long_period + 1` |
| `awesome_oscillator` | `high, low` | `fast_period` 2–499 (5), `slow_period` 3–500 (34) | single | `slow_period` |
| `cmo` | `close` | `period` 2–100 (9) | single | `period + 1` |
| `tsi` | `close` | `long_period` 3–500 (25), `short_period` 2–499 (13), `signal_period` 2–500 (13); short < long | `tsi`, `signal` | `long + short + signal - 1` |
| `keltner` | `high, low, close` | `period` 2–500 (20), `atr_period` 2–100 (10), `multiplier` (0, 10] (`2`) | `upper`, `middle`, `lower` | `max(period, atr_period)` |
| `donchian` | `high, low` | `period` 2–500 (20) | `upper`, `middle`, `lower` | `period` |
| `bollinger_percent_b` | `close` | `period` 2–500 (20), `stdev_multiplier` (0, 10] (`2`) | single | `period` |
| `bollinger_bandwidth` | `close` | `period` 2–500 (20), `stdev_multiplier` (0, 10] (`2`) | single | `period` |
| `natr` | `high, low, close` | `period` 2–100 (14) | single | `period` |
| `choppiness` | `high, low, close` | `period` 2–500 (14) | single | `period` |
| `historical_volatility` | `close` | `period` 2–500 (20), optional `annualization_periods` 1–525600 | single | `period + 1` |
| `obv` | `close, volume` | `signal_period` 2–500 (20) | `obv`, `signal` | `signal_period` |
| `cmf` | `high, low, close, volume` | `period` 2–500 (20) | single | `period` |
| `accumulation_distribution` | `high, low, close, volume` | `signal_period` 2–500 (20) | `ad`, `signal` | `signal_period` |
| `vwap` | `high, low, close, volume` | `period` 2–500 (20) | single | `period` |
| `force_index` | `close, volume` | `period` 2–500 (13) | single | `period + 1` |
| `zscore` | one OHLCV field | `period` 2–500 (20) | single | `period` |
| `percent_rank` | one OHLCV field | `period` 2–500 (20) | single | `period + 1` |

Exact formulas, operation order, and edge cases are in
signal evaluation. The conventions that are choices rather
than arithmetic:

- **Undefined on a zero divisor:** vortex (zero true-range sum), stochastic RSI (an RSI range at or
  below `1e-30`, because a flat stretch leaves RSI constant up to 64-digit rounding noise),
  ultimate oscillator (any zero true-range window; TA-Lib instead drops the term), CMO (no change),
  TSI (zero smoothed absolute momentum), %B (equal bands), choppiness (zero high-low range),
  z-score (zero stdev), and zero window volume for VWMA, CMF, and rolling VWAP.
- **Defined conventions:** KAMA treats a flat window as efficiency `1` (TA-Lib); A/D and CMF give a
  zero-range bar a multiplier of `0`; Aroon breaks ties toward the most recent bar (a flat window
  reads 100/100/0); a flat window gives TRIX, PPO, AO, NATR, bandwidth, HV, and force index `0`.
- **Supertrend** follows TradingView's `ta.supertrend` recurrence with the sign flipped
  (`direction` 1 = up, value is the lower band). The first ATR bar seeds a down trend.
- **Parabolic SAR** ports TA-Lib `SAR`: the trend starts short only when bar 1's down-move is
  positive and larger than its up-move; the first SAR is bar 0's low (or high) on bar 1; a touch
  reverses to the extreme point clamped by the prior and current bar.
- **Ichimoku has no forward displacement.** `senkou_a`/`senkou_b` are reported on the bar that
  produced them (a chart plots them `kijun_period` bars ahead), and chikou is omitted: reporting a
  displaced value at the current bar would need future data, and chikou at bar `t` is just `close`.
  The chart's "current cloud" is the same declaration with `offset: kijun_period`.
- **CMO** uses Chande's plain sums, not TA-Lib's Wilder smoothing. **Historical volatility** is
  `100 *` the sample (N − 1) stdev of `ln(close / prior close)` per bar; it is annualized by
  `sqrt(annualization_periods)` only when that parameter is present. **OBV and A/D** are cumulative
  from the first supplied bar (OBV starts at `0`), so their levels depend on where the series
  starts; the SMA `signal` line makes level-free crossovers available.
- `Decimal.ln`, `Decimal.log10`, and `Decimal.sqrt` are correctly rounded (ROUND_HALF_EVEN) in the
  64-digit engine context, so every value is deterministic and reproducible bit for bit.

Bollinger %B and bandwidth are separate kinds, not new `bollinger` series: trace keys enumerate a
kind's series, so new series would change the signal traces of existing Bollinger strategies.

### Indicator bar lag: `offset`

Any declaration except `constant` may set `offset` (integer 0–500). Bar `t` then carries the
value the unlagged series had on bar `t - offset` of the indicator's own clock (decision, extra
indicator timeframe, or HTF filter), and the first `offset` bars are undefined. Only earlier
completed bars are read, so a lag can never look ahead. Warmup is the base warmup plus `offset`.
`offset: 0` normalizes to omitted, and canonical JSON omits it, so existing documents and
fingerprints are byte-identical. The lag is applied inside the shared calculator, so research
traces, paper/live entry evaluation, ATR stops, HTF filters, and extra-TF overlays all see the
same values; trace keys are unchanged. `offset` is a sweepable indicator axis.

A prior-bar breakout is `close crosses_above donchian(20).upper` with `offset: 1` on the Donchian
declaration. Comparing a value with its own earlier bar declares the indicator twice; a sweep
must then keep both declarations' parameters equal.

### One registry for the report and the builder

`thytrader.strategies.indicator_catalog` describes every kind (category, label, one-line
summary, input policy, parameter bounds with builder defaults and help, outputs, warmup formula).
The operator `indicators` report renders it and adds `label`, `category`, `summary`,
`input_mode`, `default_input`, `parameters`, `constraints`, `warmup`, `default_warmup_bars`,
`supports_timeframe`, and `supports_offset`; historical fields keep their meaning. The builder
imports the same rows from `web/src/lib/generated/indicator-catalog.json`, written by
`scripts/export_indicator_catalog.py`; a Python test fails when the checked-in JSON drifts, and a
browser test checks its local warmup formulas against schema-computed examples. The ops contract
moves to `thytrader-ops-contract-v46` and advertises `indicator_kinds` and
`indicator_offset_runtimes` (research, paper, live). No Alembic revision.

### Templates and builder

Four research templates use the new kinds: `donchian-breakout` (close crosses the prior 20-bar
Donchian high), `supertrend-trend` (Supertrend flips up with ADX ≥ 20), `squeeze-breakout` (close
crosses the upper Bollinger band right after the bands sat inside the Keltner channel), and
`zscore-mean-reversion` (close z-score ≤ −2 while ADX < 20 or Choppiness > 61.8). The builder
gets a grouped, searchable kind picker, per-kind parameter forms with defaults and help, an
"Offset (bars ago)" field, multi-series operand groups, readable operand labels such as
`Supertrend(10, 3) · direction`, and quote-aware sizing copy. The library summary renders the
notional range with the quote currency (`10-100 USDC`) and parenthesizes mixed nested groups.

## Consequences

- Authors can build channel breakouts, adaptive and low-lag trend filters, trailing-trend
  systems, volume confirmation, squeeze setups, and statistical mean reversion without a TA
  library, and every value matches the documented formula in every runtime.
- Development cross-checked every kind with a TA-Lib reference on the same Decimal series; the
  test suite keeps recorded TA-Lib values, an independent Decimal reference implementation, hand
  worked examples, flat-price and zero-volume edge cases, and generic warmup, no-lookahead, and
  offset-shift properties for all 53 kinds.
- Agents discover the catalog, defaults, bounds, and warmup formulas from
  `thytrader-operator indicators`; a stale API image fails the ops-contract preflight instead of
  rejecting new kinds one document at a time.
- Further kinds stay additive under the same registry, report, and drift test.

## Alternatives considered

- **TA-Lib passthrough:** rejected again; it is float, not fail-closed, and its edge-case
  conventions are not ours. It is used only as a development cross-check.
- **New `bollinger` series for %B and bandwidth:** rejected; it would change the signal traces of
  existing Bollinger documents.
- **Displaced Ichimoku spans and chikou:** rejected for lookahead; `offset` covers the lagged cloud.
- **Operand-level lag:** deferred. One lag per declaration keeps warmup, trace keys, and
  multi-clock alignment unambiguous; on an extra-TF indicator an operand lag would be ambiguous
  between decision-clock and indicator-clock bars.
- **Annualized volatility by default, or OBV/A/D without a signal line:** rejected; the first hides
  a bars-per-year assumption, the second leaves only start-dependent levels to compare.
- **Hand-maintained TypeScript catalog:** rejected; the generated JSON plus drift test keeps one
  source of truth.
- **No ops-contract bump (as in ADR 0047):** rejected; the report payload changed and documents may
  now carry `offset`, which an older API would reject.
