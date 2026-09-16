# 0042: Per-indicator timeframes

- Status: Accepted
- Date: 2026-09-16
- Relates to: [0005](0005-canonical-strategy-schema.md), [0008](0008-deterministic-signal-evaluation.md),
  [0019](0019-ops-contract-identity.md), [0025](0025-multi-timeframe-htf-filter.md),
  [0031](0031-coinbase-first-platform-end-state.md),
  [0040](0040-venue-strategy-paper-live-htf-clocks.md),
  [0041](0041-paper-live-htf-filter-evaluation.md)

## Context

Indicators previously inherited the strategy decision clock. Optional `htf_filter` is a separate
closed-bar AND ([ADR 0025](0025-multi-timeframe-htf-filter.md),
[ADR 0041](0041-paper-live-htf-filter-evaluation.md)). Authors still could not put one LTF-list
indicator on `1h` while the decision clock is `5m`.

Phase 9 deferred this ([ADR 0025](0025-multi-timeframe-htf-filter.md)). Interpolating candles or
looking ahead into an in-progress coarser bar would break the complete-only contract. Extra
exchanges, shorting, and YOLO-without-confirm for live stay out.

## Decision

Keep `schema_version: "1.0"`. Allow an optional `timeframe` on each LTF-list indicator from the
ingested venue catalog (`1m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `1d`).

### Clock

- Omitted `timeframe` uses the strategy decision clock. Canonical JSON omits the field when absent
  so existing fingerprints stay stable.
- A declared extra timeframe must be a strictly coarser integer multiple of the decision clock
  (`is_valid_htf_pair`). Finer clocks, equal clocks as a required field, and non-multiples such as
  `4h`/`6h` fail closed. Authors may still write the decision clock explicitly; it evaluates as LTF.
- `constant` must omit `timeframe` (no market series).
- HTF-filter indicators must omit `timeframe`; their clock is `htf_filter.timeframe`.
- Stop and trailing ATR indicators must resolve to the decision clock.

### Alignment

At LTF close `T`, an extra-TF indicator uses the last completed bar of that timeframe whose exclusive
close is `≤ T`. Same-close bars are eligible. In-progress bars never participate. No interpolation.
Crossovers compare the mapped previous LTF bar with the current mapped bar, so they fire when that
extra TF rolls, not on every LTF bar inside it. Mixed-TF comparisons are legal because both operands
are held onto the decision clock.

`htf_filter` evaluation is unchanged. Extra-TF LTF-list values overlay the decision-clock row before
`entry.when`; the filter remains a tri-state AND.

### Warmup and research identity

LTF `data_requirements.warmup_bars` covers only decision-clock indicators. Each extra TF warms up
from the longest indicator on that clock. Research runs keep `dataset_fingerprint` as LTF and
`htf_dataset_fingerprint` as the filter dataset. Extra TFs that are not already the HTF-filter clock
require `indicator_dataset_fingerprints`: unique `{timeframe, dataset_fingerprint}` objects ordered
by increasing duration, omitted from canonical JSON when empty, each distinct from the LTF (and HTF)
fingerprint. Presence must match those unbound extra TFs. When an extra TF equals
`htf_filter.timeframe`, the HTF dataset covers it; do not duplicate that fingerprint.

### Runtime

Paper and live load complete-only last-completed extra-TF bars on the same market-data path as LTF
and HTF. Gaps, duplicates, or a missing latest completed extra-TF bar pause the deployment. They do
not bind a frozen extra-TF fingerprint at deploy.

### Ops contract

No Alembic revision. Ops contract becomes `thytrader-ops-contract-v14` with
`indicator_timeframe_runtimes` `research`, `paper`, and `live`, and `expected_schema_revision`
remaining `0026`. A v13 image fails closed until `make run`.

This extends ADR 0005 and 0025. It does not supersede 0030, 0031, 0034–0041, or rewrite paper/live
HTF-filter evaluation except to share last-completed alignment.

## Consequences

- A 5m strategy can declare a 1h EMA on the LTF indicator list and compare it with 5m RSI without a
  second `htf_filter` block.
- Support matrices list per-indicator timeframes as shipped on V1/V2/V3, paper, and live.
- Extra exchanges, shorting, and YOLO-without-confirm for live stay out.

## Alternatives considered

- **Any catalog TF including finer than LTF:** rejected; sampling the last 5m bar of a 1h decision
  clock is not a 5m strategy and is a lookahead-shaped footgun. Reuse the HTF pairing rule.
- **Expressions that mix raw TF series:** rejected by ADR 0005.
- **Rewrite paper/live HTF to a generic multi-TF engine in this slice:** rejected; compose extra-TF
  overlay with the shipped HTF path ([ADR 0041](0041-paper-live-htf-filter-evaluation.md)).
- **Bump `schema_version` to 1.1:** rejected; omitting absent `timeframe` preserves `1.0` identity.
