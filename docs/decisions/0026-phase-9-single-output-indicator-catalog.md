# 0026: Phase 9 single-output rolling extremes and stdev

- Status: Accepted
- Date: 2026-09-14
- Relates to: [0005](0005-canonical-strategy-schema.md), [0008](0008-deterministic-signal-evaluation.md),
  [0009](0009-deterministic-bar-level-backtest-engine.md), [0025](0025-multi-timeframe-htf-filter.md)

## Context

The shipped fail-closed catalog is `ema`, `sma`, `rsi`, `atr`, and `volume_sma`. Phase 9 asks for a
wider registry without TA-library passthrough, without per-indicator timeframes (ADR 0025), and
without multi-series outputs until referenceable series ids exist.

Authors already compose conditions from indicator ids and literals. The smallest useful widening is
three single-output OHLCV kinds that participate in the same comparisons and crossovers: a rolling
highest high, a rolling lowest low, and a rolling standard deviation of close.

## Decision

Keep `schema_version: "1.0"` and the existing one-value-per-bar shape (`id` + `kind` + locked
`input` + `parameters.period`). Add three kinds to the same registry used by LTF `indicators` and
HTF `htf_filter.indicators`:

| Kind | Input (locked) | Parameters | Output | First defined value |
|------|----------------|------------|--------|---------------------|
| `highest` | `high` | `period` (2–500) | max of the inclusive window | after `period` bars |
| `lowest` | `low` | `period` (2–500) | min of the inclusive window | after `period` bars |
| `stdev` | `close` | `period` (2–500) | population stdev of the inclusive window | after `period` bars |

Semantics:

- Unknown kinds, unknown fields, and unlocked inputs fail closed.
- Windows are chronological, oldest to newest, and include the current completed bar. They never
  include a future bar.
- Insufficient warmup produces `null` / undefined, not `0`. Conditions stay tri-state.
- Arithmetic uses `decimal64-half-even-v1`. Rolling max/min left-fold the window. `stdev` left-folds
  the window sum for the mean, left-folds squared deviations, divides by `period` (population, not
  sample / `N-1`), then takes context `sqrt`. A non-positive variance after that fold yields `0`.
- No TA-lib, TradingView, or provider formula passthrough. This `stdev` is not `ta.stdev`.
- MACD, Bollinger, and any other multi-series kind remain out of this slice. Per-indicator
  timeframes remain out (ADR 0025): LTF kinds stay on the top-level `timeframe`; HTF kinds stay
  inside `htf_filter`.
- Engine identifiers do not change. Adding kinds does not reinterpret existing published strategies.
  Changing a shipped formula would still require a new engine contract (ADR 0008).
- Research V1/V2/V3, paper, and live consume the same kinds on the LTF clock. Paper and live still
  reject `htf_filter`. 5m live remains Phase 13. V1 limits (long-only, one position, closed-candle
  only, no lookahead) are unchanged.

## Consequences

- Breakout, channel, and volatility-filter conditions can reference `highest`, `lowest`, and
  `stdev` ids the same way they already reference SMA/EMA/RSI.
- Operator `indicators` and the browser kind picker must list only these implemented kinds.
- Support matrices must list the new kinds as shipped and keep MACD/Bollinger and per-indicator
  timeframes as not shipped.
- Further Phase 9 kinds stay iterative. Configurable inputs, sample stdev, and multi-series outputs
  need their own contract.

## Alternatives considered

- **Kitchen-sink dump (MACD, Bollinger, stochastic, …):** rejected; multi-series outputs need
  referenceable series ids, and a large catalog would blur shipped vs planned.
- **Configurable `input` for these kinds:** deferred; the existing registry locks input per kind
  (`volume_sma` vs `sma`). Highest-high / lowest-low / close-stdev matches that pattern.
- **Sample stdev (`N-1`):** rejected for this slice because SMA already divides by `period` and
  `period >= 2` would otherwise hide a second implicit parameter (`ddof`).
- **New engine-contract version:** rejected; this is an additive catalog, not a change to shipped
  formulas or existing fingerprints.
- **Per-indicator timeframes:** rejected by ADR 0025 and still out of Phase 9.
