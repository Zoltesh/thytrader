# 0099: Operand-level indicator offsets on native clocks

- Status: Accepted
- Date: 2026-10-02
- Extends: [0005](0005-canonical-strategy-schema.md), [0008](0008-deterministic-signal-evaluation.md),
  [0042](0042-per-indicator-timeframes.md), [0044](0044-parameter-sweeps-wfo-stitched-equity.md),
  [0093](0093-signal-based-exits.md), [0096](0096-reference-instruments.md)
- Supersedes the operand-level-lag deferral in
  [0086](0086-indicator-catalog-expansion-and-offset.md); declaration offsets remain supported.

## Context

A squeeze strategy previously declared Bollinger and Keltner twice to compare their prior bar
while also using today's bands. Sweeping one declaration's parameters left its prior-bar copy
unchanged, altering the intended rule. One shared definition should serve both reads.

## Decision

An indicator operand may add `offset`, a strict integer from 0 through 500:
`{"indicator":"bands","series":"upper","offset":1}`. It reads that output from the
referenced indicator's **own clock**, before alignment to the decision clock. This is the
strategy clock for ordinary indicators, the declaration's extra timeframe for extra-clock
indicators, the reference timeframe for sourced indicators, or the filter clock inside an HTF
filter. Only completed bars are eligible. Missing history remains undefined, including under
NOT. Literals cannot carry an offset; constant operands reject a positive offset.

The declaration offset still applies to all its outputs. An operand adds its own lag to that
already-lagged output. Crossovers compare both operands' lagged values at the current and previous
decision closes, retaining the existing last-completed-bar alignment and crossover semantics.

Warmup includes each indicator's formula, declaration lag, and largest operand lag referenced by
entry or signal-exit rules. HTF warmup includes filter reads and extra-clock reads sharing its
dataset. Reference and extra-clock coverage is derived consistently for binding, publication,
research, and execution. Parameter candidates recompute these requirements after substitution.

Omitted and zero operand offsets are omitted from canonical JSON. Existing documents, snapshots,
traces, and fingerprints remain unchanged. New lagged reads add trace keys `id@N` or `id.series@N`
alongside the original outputs, so journals and research expose the exact values compared.

The builder offers independent left/right "Bars ago" fields and validates lag bounds and warmup.
New squeeze templates use one Bollinger and one Keltner declaration and expose their parameter
axes. Existing saved strategies and running snapshots are not rewritten.

Ops contract becomes `thytrader-ops-contract-v59`, adding
`indicator_operand_offset_runtimes: ["research", "paper", "live"]`. Alembic stays `0059`.
No fill-rule change or simulation-semantics bump is required.

## Alternatives considered

- **Linked parameter axes:** would preserve duplicated declarations and require linked-target
  machinery in every study contract. Operand reads solve the authoring problem directly.
- **Decision-clock operand lag:** would make "one bar ago" on a 1d indicator mean a previous 5m
  decision in a 5m strategy, often the same daily value. Native-clock lag is consistent with
  existing declaration offsets and is applied before alignment.
- **Remove declaration offsets or rewrite saved strategies:** would disturb existing snapshots
  and indicator references used by stops. Both forms coexist without modifying running rules.
