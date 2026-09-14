# 0028: Phase 9 identity OHLCV and constant-level series

- Status: Accepted
- Date: 2026-09-14
- Relates to: [0005](0005-canonical-strategy-schema.md), [0008](0008-deterministic-signal-evaluation.md),
  [0025](0025-multi-timeframe-htf-filter.md), [0026](0026-phase-9-single-output-indicator-catalog.md),
  [0027](0027-phase-9-roc-williams-cci.md)

## Context

Crossovers still require two indicator operands. Comparisons may use a `literal`, but authors cannot
write `RSI crosses 40` or `close crosses SMA` without a named series for the candle field or the
level. ADR 0027 deferred those kinds because they change the `parameters.period` shape.

## Decision

Keep `schema_version: "1.0"` and the one-value-per-bar registry used by LTF `indicators` and HTF
`htf_filter.indicators`. Add two kinds. Existing period kinds are unchanged.

| Kind | Input | Parameters | Output | First defined value |
|------|-------|------------|--------|---------------------|
| `identity` | one of `open`, `high`, `low`, `close`, `volume` (author-selected, not a hidden rolling input) | `{}` | that candle field | first completed bar |
| `constant` | omitted | `{ "value": "<DecimalText>" }` | that exact finite decimal on every bar | first completed bar |

Semantics:

- Unknown kinds, unknown fields, mixed parameter objects, and unlocked inputs fail closed.
  `identity` rejects a period, a value, and the ATR HLC tuple. `constant` rejects `input` and
  `period`. Period kinds still reject empty parameters and `value`.
- Canonical JSON omits `input` when it is absent so constant kinds do not fingerprint a null.
  Existing published strategies always declare `input` and keep their fingerprints.
- `identity` is defined on every completed bar in the supplied series. `constant` repeats the
  canonical `value` on every bar. Neither produces `null` after the first bar. Warmup is 1.
- Arithmetic uses `decimal64-half-even-v1` only when a later kind divides; these kinds copy or
  repeat already-canonical decimals.
- Crossovers still require two indicator operands. `RSI crosses 40` is `rsi` vs `constant`.
  `close crosses SMA` is `identity` (close) vs `sma`. Literals remain legal for `greater_than*` /
  `less_than*` / `equals`.
- `identity` may select `open`. That does not unlock SMA-of-open or other rolling kinds.
- No TA-lib passthrough. No MACD/Bollinger. No sample stdev. No configurable inputs on rolling
  kinds. No per-indicator timeframes (ADR 0025). Engine identifiers do not change.
- Research V1/V2/V3, paper, and live consume the same kinds on the LTF clock. Paper and live still
  reject `htf_filter`. 5m live remains Phase 13.

## Consequences

- Named candle fields and named levels participate in comparisons and crossovers like any other
  single-output id.
- Operator `indicators` lists `parameter_kind` (`period` / `none` / `value`) so agents do not assume
  every kind has `period`.
- The browser kind picker must offer an identity source and a constant value field.
- Further Phase 9 kinds stay iterative. Configurable rolling inputs, sample stdev, and multi-series
  outputs need their own contract.

## Alternatives considered

- **Allow crossover vs `literal`:** rejected; it would special-case one operator instead of giving
  levels a named, fingerprintable series that HTF filters can also declare.
- **Five locked kinds (`close`, `high`, …):** rejected as catalog noise; `identity` plus a bounded
  source field is one contract.
- **Dummy `input: "close"` on constant:** rejected; unused locked fields are dishonest.
- **Dummy `period` on identity/constant:** rejected; that is the shape ADR 0027 called out.
- **New engine-contract version:** rejected; additive catalog, no change to shipped formulas.
- **Per-indicator timeframes:** rejected by ADR 0025 and still out of Phase 9.
