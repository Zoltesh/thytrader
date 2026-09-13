# 0020: Complete-only 15m historical datasets

- Status: Accepted
- Date: 2026-09-13
- Relates to: [0014](0014-watchlist-and-5m-research.md), [0016](0016-longer-complete-5m-datasets.md),
  [0018](0018-5m-paper-not-live.md), [0019](0019-ops-contract-identity.md)

## Context

Phase 2A already publishes complete-only 1h and 5m Parquet datasets through the market-data worker.
Phase 7 continues that contract for remaining timeframes; this ADR is the 15m dataset slice only.
Strategy schema, paper, and live clocks are `1h` or `5m` (live `1h` only). Widening those clocks at
the same time as ingest would silently let research and execution treat `15m` as a strategy timeframe.

## Decision

- Add `CandleInterval.FIFTEEN_MINUTES` (`15m`) for **dataset** ingest, publication, verification,
  gap classification, diagnostics, and latest-dataset catalog.
- Keep the existing complete-only pipeline: closed UTC bars, no interpolation, worker-only
  publication, fingerprint-addressed Parquet plus manifest, UTC-day chunks, `watch_complete`.
- Do **not** widen `StrategyDefinition.timeframe`, paper/live ops-contract clocks, or research
  warmup inference to `15m`. Dataset binding still requires matching product and timeframe, so a
  1h/5m strategy cannot consume a 15m dataset.
- Watchlist CHECK and ops-contract `expected_schema_revision` move to Alembic `0017`. Bump
  `OPS_CONTRACT_ID` to `thytrader-ops-contract-v3` per [0019](0019-ops-contract-identity.md).

## Consequences

- Operators can watch, ingest, inspect, and catalog 15m coverage the same way as 5m.
- 15m fingerprints appear in the verified dataset catalog; they are not a research or paper clock.
- 30m, 6h, and 1d remain planned Phase 7 slices. Multi-timeframe strategy semantics remain Phase 8.

## Alternatives considered

- Wait until strategy `timeframe` includes `15m`: rejected; ingest is independently useful and the
  documented next Phase 7 slice.
- Add 15m to strategy, paper, and live together: rejected; execution clocks are an explicit
  follow-up, not a side effect of datasets.
- Interpolate incomplete 15m days: rejected; completeness stays binary and classified.
