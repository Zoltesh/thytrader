# 0025: Multi-timeframe HTF filter + LTF entry

- Status: Accepted
- Date: 2026-09-14
- Relates to: [0005](0005-canonical-strategy-schema.md), [0007](0007-immutable-research-run-specifications.md),
  [0008](0008-deterministic-signal-evaluation.md), [0009](0009-deterministic-bar-level-backtest-engine.md),
  [0018](0018-5m-paper-not-live.md), [0020](0020-complete-only-15m-datasets.md),
  [0021](0021-complete-only-30m-datasets.md), [0022](0022-complete-only-6h-datasets.md),
  [0023](0023-complete-only-1d-datasets.md), [0026](0026-phase-9-single-output-indicator-catalog.md)

## Context

Phase 7 shipped complete-only `15m`, `30m`, `6h`, and `1d` datasets. Strategy `timeframe` remains the
LTF decision clock and is still only `1h` or `5m`. Paper may use either; live remains `1h`. Dataset
presence must not silently become a paper or live clock.

Operators need a combined higher-timeframe filter and lower-timeframe entry without Python, JavaScript,
or expression strings (ADR 0005). Per-indicator timeframes would allow mixed-TF crossovers and sticky
filters that are harder to validate and fingerprint. This ADR records the smallest fail-closed
declarative extension that uses Phase 7 datasets as HTF filter inputs.

## Decision

Keep top-level `timeframe` as the LTF decision clock (`1h` | `5m`). Add one optional `htf_filter`
block on the same `schema_version: "1.0"` document:

```json
{
  "timeframe": "5m",
  "htf_filter": {
    "timeframe": "1h",
    "data_requirements": {
      "warmup_bars": 50,
      "required_fields": ["open", "high", "low", "close", "volume"]
    },
    "indicators": [],
    "when": { "all": [] }
  }
}
```

Rules:

- Unknown fields fail closed. `htf_filter` omitted is equivalent to `null` and is stripped from
  canonical JSON so existing single-timeframe fingerprints do not change.
- HTF timeframe ∈ `{15m, 30m, 1h, 6h, 1d}`, strictly coarser than LTF, and an integer multiple of LTF
  duration. `5m` LTF may use `15m`/`30m`/`1h`/`6h`/`1d`. `1h` LTF may use `6h`/`1d` only.
- HTF `when` may reference only HTF indicators. LTF `entry.when` and the ATR stop may reference only
  LTF indicators. Indicator ids are unique across both lists. Indicator kinds are the fail-closed
  catalog current at evaluation (EMA/SMA/RSI/ATR/`volume_sma` at acceptance; extended by
  [ADR 0026](0026-phase-9-single-output-indicator-catalog.md)).
- Combined entry is the tri-state AND of HTF filter and LTF entry (undefined in either input is
  undefined).
- Evaluation uses closed candles only. At LTF close `T`, HTF values come from the last HTF bar whose
  exclusive close is `≤ T`. A same-close HTF bar is eligible; an in-progress HTF bar is never used.
  Crossovers compare the mapped previous LTF bar's HTF values with the current mapped HTF values, so
  they fire when the HTF bar rolls, not on every LTF bar inside the next HTF period.
- Research runs keep `dataset_fingerprint` as the LTF dataset and add optional
  `htf_dataset_fingerprint`. The two identities must differ. Presence must match `htf_filter`.
  Eligibility binds and fingerprints both datasets. HTF coverage is last-completed bars plus HTF
  warmup; HTF datasets do not need a next-open fill candle.
- Research engines V1, V2, and V3 share the signal stage and therefore consume `htf_filter`. Paper
  and live reject published strategies that declare `htf_filter` rather than evaluating LTF-only or
  inventing HTF candles. 5m live remains Phase 13.

This extends ADR 0005. It does not supersede it, widen the indicator catalog, add positions, or treat
`15m`/`30m`/`6h`/`1d` as LTF, paper, or live clocks.

## Consequences

- Authors can declare HTF trend filters against Phase 7 datasets while keeping one immutable published
  version for research.
- Canonical strategy and research-run identity now include the HTF definition and HTF dataset when
  present, without rewriting historical single-TF bytes.
- Paper/live remain single-clock until a later slice binds HTF candles in the execution worker.
- Support matrices must list research V1/V2/V3 as supporting HTF filters and paper/live as rejecting
  them.
- The indicator catalog later gained `highest`, `lowest`, and `stdev` ([ADR 0026](0026-phase-9-single-output-indicator-catalog.md))
  without changing HTF alignment or adding per-indicator timeframes.

## Alternatives considered

- **Per-indicator `timeframe`:** rejected for this slice; mixed-TF crossovers and sticky filters are
  larger than the HTF-filter + LTF-entry product intent.
- **Expressions or Python HTF predicates:** rejected by ADR 0005.
- **Silently ignore `htf_filter` in paper/live:** rejected; that would trade without the published
  filter.
- **Treat Phase 7 dataset presence as a paper/live clock:** rejected by ADRs 0020–0023 and 0018.
- **Bump `schema_version` to 1.1:** rejected; omitting `htf_filter` preserves `1.0` identity for
  existing documents.
