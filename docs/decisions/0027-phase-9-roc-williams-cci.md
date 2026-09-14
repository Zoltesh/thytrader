# 0027: Phase 9 single-output ROC, Williams %R, and CCI

- Status: Accepted
- Date: 2026-09-14
- Relates to: [0005](0005-canonical-strategy-schema.md), [0008](0008-deterministic-signal-evaluation.md),
  [0025](0025-multi-timeframe-htf-filter.md), [0026](0026-phase-9-single-output-indicator-catalog.md)

## Context

Phase 9 slice 1 added rolling extremes and population stdev. Slice 2 continues the same
fail-closed, single-output registry: no TA-library passthrough, no per-indicator timeframes
(ADR 0025), and no multi-series outputs until referenceable series ids exist.

Authors already compose momentum and mean-reversion conditions from RSI, ATR, and stdev. The
smallest useful widening is three more one-value-per-bar kinds that participate in the same
comparisons and crossovers: close rate-of-change, Williams %R, and CCI.

## Decision

Keep `schema_version: "1.0"` and the existing one-value-per-bar shape (`id` + `kind` + locked
`input` + `parameters.period`). Add three kinds to the same registry used by LTF `indicators` and
HTF `htf_filter.indicators`:

| Kind | Input (locked) | Parameters | Output | First defined value |
|------|----------------|------------|--------|---------------------|
| `roc` | `close` | `period` (2–500) | `100 * (close - close[period]) / close[period]` | after `period + 1` bars |
| `williams_r` | `high, low, close` | `period` (2–100) | `(highest_high - close) / (highest_high - lowest_low) * -100` | after `period` bars |
| `cci` | `high, low, close` | `period` (2–100) | `(TP - SMA(TP)) / (0.015 * MAD)` | after `period` bars |

Semantics:

- Unknown kinds, unknown fields, and unlocked inputs fail closed.
- Windows and lookbacks are chronological, oldest to newest, and include the current completed bar.
  They never include a future bar. `roc` lookback `close[period]` is exactly `period` completed bars
  ago, not `period - 1`.
- Insufficient warmup produces `null` / undefined, not `0`. A zero divisor also produces undefined:
  `roc` when the lookback close is `0`; `williams_r` when the window high equals the window low;
  `cci` when mean absolute deviation is `0`. Conditions stay tri-state.
- Arithmetic uses `decimal64-half-even-v1`. Rolling max/min reuse the slice-1 left-fold. `cci`
  typical price is `(high + low + close) / 3`. Its SMA is the shipped SMA left-fold of typical
  price. MAD is the chronological left-fold of `abs(TP - SMA)` divided by `period` (population, not
  sample / `N-1`). The Lambert constant is exact `0.015`. Division uses `(TP - SMA) / (0.015 * MAD)`
  with the product completed before the divide. Williams %R divides the close displacement by the
  window range, then multiplies by `-100`.
- Canonical signal-trace decimal text accepts an optional leading minus so ROC and Williams %R can
  appear in traces. Existing unsigned traces remain valid. Engine identifiers do not change.
- No TA-lib, TradingView, or provider formula passthrough. These kinds are not `ta.roc` / `ta.willr`
  / `ta.cci` identities.
- MACD, Bollinger, stochastic, identity OHLCV series, constant-level series, configurable inputs,
  and sample stdev remain out of this slice. Per-indicator timeframes remain out (ADR 0025).
- Engine identifiers do not change. Adding kinds does not reinterpret existing published strategies.
- Research V1/V2/V3, paper, and live consume the same kinds on the LTF clock. Paper and live still
  reject `htf_filter`. 5m live remains Phase 13.

## Consequences

- Momentum, oversold, and mean-reversion conditions can reference `roc`, `williams_r`, and `cci`
  ids the same way they already reference RSI/ATR/stdev.
- Operator `indicators` and the browser kind picker must list only these implemented kinds.
- Support matrices must list the new kinds as shipped and keep MACD/Bollinger and per-indicator
  timeframes as not shipped.
- Further Phase 9 kinds stay iterative. Identity OHLCV series, constant-level series, configurable
  inputs, and multi-series outputs need their own contract.

## Alternatives considered

- **Identity close/volume plus a constant series:** rejected for this slice because they change the
  `parameters.period` shape; they remain a later single-output increment.
- **Kitchen-sink dump (MACD, Bollinger, stochastic, ADX, …):** rejected; multi-series outputs need
  referenceable series ids.
- **Configurable `input` for ROC:** deferred; the existing registry locks input per kind.
- **New engine-contract version:** rejected; this is an additive catalog, not a change to shipped
  formulas or existing fingerprints.
- **Per-indicator timeframes:** rejected by ADR 0025 and still out of Phase 9.
