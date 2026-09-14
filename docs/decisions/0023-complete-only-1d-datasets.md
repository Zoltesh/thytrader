# 0023: Complete-only 1d historical datasets

- Status: Accepted
- Date: 2026-09-14
- Relates to: [0014](0014-watchlist-and-5m-research.md), [0016](0016-longer-complete-5m-datasets.md),
  [0019](0019-ops-contract-identity.md), [0020](0020-complete-only-15m-datasets.md),
  [0021](0021-complete-only-30m-datasets.md), [0022](0022-complete-only-6h-datasets.md)

## Context

Phase 7 continues the complete-only Parquet + manifest contract beyond 1h/5m/15m/30m/6h. Six-hour
datasets shipped in [0022](0022-complete-only-6h-datasets.md). This ADR is the 1d dataset slice
only. Strategy schema, paper, and live clocks stay `1h` or `5m` (live `1h` only). Widening those
clocks at the same time as ingest would silently let research and execution treat `1d` as a
strategy timeframe.

## Decision

- Add `CandleInterval.ONE_DAY` (`1d`) for **dataset** ingest, publication, verification,
  gap classification, diagnostics, and latest-dataset catalog.
- Daily bars have exact one-day duration and UTC close alignment at the day boundary `00:00`.
  A complete UTC day contains exactly one aligned candle.
- Keep the existing complete-only pipeline: closed UTC bars, no interpolation, worker-only
  publication, fingerprint-addressed Parquet plus manifest, UTC-day chunks, `watch_complete`.
- Do **not** widen `StrategyDefinition.timeframe`, paper/live ops-contract clocks, or research
  warmup inference to `1d`. Dataset binding still requires matching product and timeframe, so a
  1h/5m strategy cannot consume a 1d dataset.
- Watchlist CHECK and ops-contract `expected_schema_revision` move to Alembic `0020`. Bump
  `OPS_CONTRACT_ID` to `thytrader-ops-contract-v6` per [0019](0019-ops-contract-identity.md).
  Downgrade restores the 1h/5m/15m/30m/6h constraint; existing `1d` rows must be removed first.

## Consequences

- Operators can watch, ingest, inspect, and catalog 1d coverage the same way as 6h.
- 1d fingerprints appear in the verified dataset catalog; they are not a research or paper clock.
- Phase 7 data-loop hardening remains planned. Multi-timeframe strategy semantics remain Phase 8.

## Alternatives considered

- Wait until strategy `timeframe` includes `1d`: rejected; ingest is independently useful and the
  documented next Phase 7 slice.
- Add 1d to strategy, paper, and live together: rejected; execution clocks are an explicit
  follow-up, not a side effect of datasets.
- Interpolate incomplete 1d days: rejected; completeness stays binary and classified.
