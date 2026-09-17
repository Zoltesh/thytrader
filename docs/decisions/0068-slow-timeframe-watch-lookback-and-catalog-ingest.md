# 0068: Slow-timeframe watch lookback and catalog/ingest hardening

- Status: Accepted
- Date: 2026-09-17

## Context

The 17 Sep portfolio-research ops run exposed catalog and ingest defects on a 48-cell
watch grid:

- Partial islands stopped advancing because ingest requests cleared while
  `watch_complete` was still false and `fill-gaps` reused the same reconcile
  short-circuit as `ingest`.
- `watch_complete` flipped on every closed bar while a single worker serviced other
  targets, even when freshness was still current.
- Operator `data-catalog` mixed island and watch semantics (`complete` vs
  `watch_complete` / `sparsity`) and omitted worker `failure_code` facts present on
  the ingestion endpoint.
- `inspect-gaps` timed out on 129,600-bar 1m watches because it probed and listed the
  entire window in one response.
- A 90-day global lookback leaves 1d and 6h research with too few closed bars for
  robust walk-forward.

Interpolation remains forbidden.

## Decision

- Keep 2,160-hour (90-day) ceilings for `1m`, `5m`, `15m`, `30m`, and `1h` watches.
  Raise the ceiling to 8,760 hours (365 days) for `2h`, `4h`, `6h`, and `1d`.
- Do not clear a watchlist ingest request until `watch_complete` is true. When a
  partial island is published, the worker keeps the request pending and skips the
  current-island reconcile short-circuit on the next cycle.
- `POST /api/v1/data/fill-gaps` queues the same worker path but is documented as
  continuation ingest (not a price-fill mutation). The CLI `fill-gaps` command calls
  this route.
- Treat `watch_complete` as true when freshness is `fresh` and coverage is at most one
  closed bar behind the aligned boundary (incremental lag on large grids).
- Split catalog sparsity: `sparsity` is island-only; `watch_sparsity` reflects the
  configured watch window. Surface `failure_code` / `failure_message` on catalog rows.
- `inspect-gaps` probes exchange coverage in UTC-day chunks and returns `gap_summary`
  counts with a capped `gaps` sample list.
- Use chunked forward ingest when an incremental plan spans more than one UTC day.
- Stop chunked backfill at the first incomplete day and record the newest complete
  island without treating the run as watch-complete.

## Consequences

- Agents can bind year-long 1d/6h datasets without raising sub-daily interval caps.
- Large grids stay stable between worker cycles when candles are still fresh.
- Catalog rows align with ingestion failure detail; BONK `1m` `incomplete_range`
  failures are visible without scraping logs.
- Alembic `0041` widens the watchlist CHECK (revises #90's `0040` marker); ops contract
  `thytrader-ops-contract-v28` advertises the change.

## Alternatives considered

- Raise the global 2,160-hour ceiling for every timeframe: rejected because 1m at 365
  days exceeds `MAX_HISTORICAL_INTERVAL_COUNT`.
- Interpolate missing prefix bars to “finish” a watch quickly: rejected.
- Keep `fill-gaps` as an ingest alias: rejected; continuation must bypass reconcile.
