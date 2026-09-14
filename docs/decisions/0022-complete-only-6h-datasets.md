# 0022: Complete-only 6h historical datasets

- Status: Accepted
- Date: 2026-09-14
- Relates to: [0014](0014-watchlist-and-5m-research.md), [0016](0016-longer-complete-5m-datasets.md),
  [0019](0019-ops-contract-identity.md), [0020](0020-complete-only-15m-datasets.md),
  [0021](0021-complete-only-30m-datasets.md)

## Context

Phase 7 continues the complete-only Parquet + manifest contract beyond 1h/5m/15m/30m. Thirty-minute
datasets shipped in [0021](0021-complete-only-30m-datasets.md). This ADR is the 6h dataset slice
only. Strategy schema, paper, and live clocks stay `1h` or `5m` (live `1h` only). Widening those
clocks at the same time as ingest would silently let research and execution treat `6h` as a
strategy timeframe.

## Decision

- Add `CandleInterval.SIX_HOURS` (`6h`) for **dataset** ingest, publication, verification,
  gap classification, diagnostics, and latest-dataset catalog.
- Six-hour bars have exact six-hour duration and UTC close alignment at `00:00`, `06:00`, `12:00`,
  and `18:00`. A complete UTC day contains exactly four aligned candles.
- Keep the existing complete-only pipeline: closed UTC bars, no interpolation, worker-only
  publication, fingerprint-addressed Parquet plus manifest, UTC-day chunks, `watch_complete`.
- Do **not** widen `StrategyDefinition.timeframe`, paper/live ops-contract clocks, or research
  warmup inference to `6h`. Dataset binding still requires matching product and timeframe, so a
  1h/5m strategy cannot consume a 6h dataset.
- Watchlist CHECK and ops-contract `expected_schema_revision` move to Alembic `0019`. Bump
  `OPS_CONTRACT_ID` to `thytrader-ops-contract-v5` per [0019](0019-ops-contract-identity.md).
  Downgrade restores the 1h/5m/15m/30m constraint; existing `6h` rows must be removed first.

## Consequences

- Operators can watch, ingest, inspect, and catalog 6h coverage the same way as 30m.
- 6h fingerprints appear in the verified dataset catalog; they are not a research or paper clock.
- 1d remains a planned Phase 7 slice. Multi-timeframe strategy semantics remain Phase 8.

## Alternatives considered

- Wait until strategy `timeframe` includes `6h`: rejected; ingest is independently useful and the
  documented next Phase 7 slice.
- Add 6h to strategy, paper, and live together: rejected; execution clocks are an explicit
  follow-up, not a side effect of datasets.
- Interpolate incomplete 6h days: rejected; completeness stays binary and classified.
