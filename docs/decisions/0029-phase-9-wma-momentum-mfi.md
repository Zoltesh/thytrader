# 0029: Phase 9 single-output WMA, momentum, and MFI

- Status: Accepted
- Date: 2026-09-15
- Relates to: [0005](0005-canonical-strategy-schema.md), [0008](0008-deterministic-signal-evaluation.md),
  [0025](0025-multi-timeframe-htf-filter.md), [0026](0026-phase-9-single-output-indicator-catalog.md),
  [0027](0027-phase-9-roc-williams-cci.md), [0028](0028-phase-9-identity-constant.md)

## Context

Phase 9 slices 1–3 added rolling extremes, population stdev, ROC, Williams %R, CCI, identity OHLCV,
and constant levels. Slice 4 continues the same fail-closed, single-output registry: no TA-library
passthrough, no per-indicator timeframes (ADR 0025), and no multi-series outputs until referenceable
series ids exist.

Authors already compose trend and oscillator conditions from SMA/EMA/RSI/ROC. The smallest useful
widening is three more one-value-per-bar kinds: a weighted moving average, absolute close momentum
(the non-percent sibling of ROC), and money-flow index (the volume sibling of RSI).

## Decision

Keep `schema_version: "1.0"` and the one-value-per-bar registry used by LTF `indicators` and HTF
`htf_filter.indicators`. Add three kinds. Existing kinds are unchanged.

| Kind | Input (locked) | Parameters | Output | First defined value |
|------|----------------|------------|--------|---------------------|
| `wma` | `close` | `period` (2–500) | weighted mean of the inclusive window | after `period` bars |
| `momentum` | `close` | `period` (2–500) | `close - close[period]` | after `period + 1` bars |
| `mfi` | `high, low, close, volume` | `period` (2–100) | `100 * positive / (positive + negative)` | after `period + 1` bars |

Semantics:

- Unknown kinds, unknown fields, and unlocked inputs fail closed. `wma` and `momentum` reject any
  source other than `close`. `mfi` rejects any input other than the canonical ordered array
  `["high", "low", "close", "volume"]`. Period kinds still reject empty parameters and `value`.
- Windows and lookbacks are chronological, oldest to newest, and include the current completed bar.
  They never include a future bar. `momentum` lookback `close[period]` is exactly `period` completed
  bars ago, matching ROC. `mfi` signs each of the last `period` typical-price changes against the
  immediately previous typical price, so it also needs `period + 1` bars.
- Insufficient warmup produces `null` / undefined, not `0`. Conditions stay tri-state.
- Arithmetic uses `decimal64-half-even-v1`. `wma` left-folds `value * weight` with oldest weight `1`
  and newest weight `period`, then divides by the left-folded weight sum
  `period * (period + 1) / 2`. `momentum` subtracts the lookback close; a zero lookback close is a
  defined difference, not a divisor.
- `mfi` typical price is `(high + low + close) / 3`, the same as CCI. Raw money flow is
  `TP * volume`. A bar whose typical price is greater than the previous typical price adds to
  positive money flow; less than adds to negative; equal adds to neither. The window sums those
  signed flows over the last `period` completed changes. Canonical MFI is
  `100 * positive / (positive + negative)` with the sum completed before the divide. A zero total
  (no signed money flow in the window) yields undefined, not `50` or `100`. One-sided windows are
  defined: all-positive is `100`, all-negative is `0`.
- No TA-lib, TradingView, or provider formula passthrough. These kinds are not `ta.wma` / `ta.mom` /
  `ta.mfi` identities.
- MACD, Bollinger, stochastic, ADX, configurable rolling inputs, and sample stdev remain out of this
  slice. Per-indicator timeframes remain out (ADR 0025).
- Engine identifiers do not change. Adding kinds does not reinterpret existing published strategies.
- Research V1/V2/V3, paper, and live consume the same kinds on the LTF clock. Paper and live still
  reject `htf_filter`. 5m live remains Phase 13.

## Consequences

- Weighted trend, absolute momentum, and volume-confirmation conditions can reference `wma`,
  `momentum`, and `mfi` ids the same way they already reference SMA/EMA/RSI/ROC.
- Operator `indicators` and the browser kind picker must list only these implemented kinds.
- Support matrices must list the new kinds as shipped and keep MACD/Bollinger and per-indicator
  timeframes as not shipped.
- Further Phase 9 kinds stay iterative. Configurable rolling inputs, sample stdev, and multi-series
  outputs need their own contract.

## Alternatives considered

- **Kitchen-sink dump (MACD, Bollinger, stochastic, ADX, …):** rejected; multi-series outputs need
  referenceable series ids, and ADX's Wilder seed is a separate formula contract.
- **Configurable `input` for WMA:** deferred; the existing registry locks input per kind.
- **MFI as 100 when negative money flow is 0:** rejected in favor of the equivalent
  `100 * positive / (positive + negative)` form, which defines one-sided windows without a zero
  divisor and still fails closed when both flows are 0.
- **New engine-contract version:** rejected; this is an additive catalog, not a change to shipped
  formulas or existing fingerprints.
- **Per-indicator timeframes:** rejected by ADR 0025 and still out of Phase 9.
