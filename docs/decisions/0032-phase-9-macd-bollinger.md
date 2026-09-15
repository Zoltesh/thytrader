# 0032: Phase 9 multi-series MACD and Bollinger

- Status: Accepted
- Date: 2026-09-15
- Relates to: [0005](0005-canonical-strategy-schema.md), [0008](0008-deterministic-signal-evaluation.md),
  [0025](0025-multi-timeframe-htf-filter.md), [0026](0026-phase-9-single-output-indicator-catalog.md),
  [0027](0027-phase-9-roc-williams-cci.md), [0028](0028-phase-9-identity-constant.md),
  [0029](0029-phase-9-wma-momentum-mfi.md)

## Context

Phase 9 slices 1–4 added fail-closed single-output kinds. Slice 5 is MACD and Bollinger. Those
studies emit more than one value per bar, so conditions cannot keep treating an indicator `id` as
the sole series. Earlier ADRs deferred this slice until referenceable series ids exist. Per-indicator
timeframes remain out of Phase 9 (ADR 0025).

## Decision

Keep `schema_version: "1.0"`. Introduce an optional operand `series` field and two multi-series
kinds on the same LTF/HTF registry. Existing single-output kinds are unchanged. Engine identifiers
do not change.

### Series-id contract

- Single-output operands remain `{ "indicator": "<id>" }` and **must omit** `series`.
- Multi-series operands are `{ "indicator": "<id>", "series": "<name>" }`. `series` is required and
  must be one of that kind's declared output names.
- Unknown series names, `series` on a single-output kind, and a missing `series` on a multi-series
  kind fail closed.
- Canonical JSON omits `series` when absent so already-published single-output fingerprints stay
  stable.
- Trace and evaluator keys for multi-series values are `{id}.{series}`. Indicator ids cannot contain
  `.`, so these keys cannot collide with another indicator id. Single-output traces still use `{id}`.

### Kinds

| Kind | Input (locked) | Parameters | Outputs | First defined values |
|------|----------------|------------|---------|----------------------|
| `macd` | `close` | `fast_period`, `slow_period`, `signal_period` (each 2–500; fast < slow) | `macd`, `signal`, `histogram` | MACD line after `slow_period` bars; signal and histogram after `slow_period + signal_period - 1` bars |
| `bollinger` | `close` | `period` (2–500), `stdev_multiplier` (plain decimal `> 0` and `≤ 10`) | `middle`, `upper`, `lower` | after `period` bars |

Semantics:

- Unknown kinds, unknown fields, and unlocked inputs fail closed.
- Arithmetic uses `decimal64-half-even-v1`. Windows include the current completed bar and never a
  future bar. Insufficient warmup produces `null` / undefined, not `0`. Conditions stay tri-state.
- `macd` line is shipped EMA(`close`, `fast_period`) minus shipped EMA(`close`, `slow_period`).
  `signal` is the same SMA-seeded EMA recurrence applied to the suffix of defined MACD-line values
  with period `signal_period`. `histogram` is `macd - signal`. Undefined until both operands of a
  difference are defined.
- `bollinger` `middle` is the shipped SMA of close. Band width uses the shipped population `stdev`
  of the same inclusive window, not sample / `N-1`. `upper = middle + multiplier * stdev` and
  `lower = middle - multiplier * stdev`. A zero stdev yields equal bands at the middle (defined).
- No TA-lib, TradingView, or provider formula passthrough. These kinds are not `ta.macd` /
  `ta.bbands` identities.
- Stochastic, ADX, configurable rolling inputs, sample stdev, and per-indicator timeframes remain
  out of this slice.
- Research V1/V2/V3, paper, and live consume the same kinds on the LTF clock. Paper and live still
  reject `htf_filter`. 5m live remains Phase 13.

## Consequences

- Authors can write MACD crossovers (`macd` vs `signal`) and band comparisons (`identity` close vs
  `bollinger` `lower` / `upper`) without inventing extra indicator ids.
- Operator `indicators` lists `outputs` for these kinds and `parameter_kind` `macd` / `bollinger`.
- Support matrices list MACD/Bollinger as shipped and keep per-indicator timeframes as not shipped.
- Adding kinds does not reinterpret existing published strategies.

## Alternatives considered

- **Flattened operand ids (`macd_12.histogram` as the indicator id):** rejected; indicator ids stay
  the declared definition identity, and series is an explicit second coordinate.
- **One value per kind (MACD histogram only, or Bollinger %B only):** rejected; authors need the
  line/signal crossover and the three bands.
- **Kitchen-sink dump (stochastic, ADX, …):** rejected; ADX's Wilder seed and stochastic's extra
  series remain separate contracts.
- **New engine-contract version:** rejected; this is an additive catalog plus an omitted-when-absent
  operand field, not a change to shipped formulas.
- **Per-indicator timeframes:** rejected by ADR 0025 and still out of Phase 9.
