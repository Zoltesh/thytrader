# ADR 0072: Catalog health — bounded gap inspection, self-complete ingest, live heartbeats

## Status

Accepted (2026-09-17)

## Context

The 17 Sep QA follow-up found three catalog/ingest defects that ADR 0068 did not finish:

3. DOGE 1m `inspect-gaps` exceeded 180s and wedged the API. Capping the JSON `gaps` sample at 200
   did not bound server-side local scans, exchange probes, or bar classification.
4. Queuing ingest once for a 24-cell slow-clock grid left partial islands. `ingest_once` tried to
   walk the remaining lookback in one cycle, so other cells starved and operators still needed
   extra `fill-gaps` calls.
17. Portfolio-scale continuation left the market-data worker heartbeat stale by 1,135s. Health used
   `min(interval, 5s)` as the stale window while the worker only touched the heartbeat at cycle
   start, then blocked on a single cell. Research mutations failed closed until that worker restarted.

Interpolation remains forbidden.

## Decision

1. **Bounded gap inspection** — `inspect-gaps` applies a server-side time budget, local-row budget,
   probe-day budget, and classification-bar budget. Exhaustion returns HTTP 200 with
   `truncated=true`, `scanned_bar_count`, a partial `gap_summary`, and a warning. Unprobed bars stay
   unknown rather than `exchange_unavailable`. Listed `gaps` remain capped.
2. **Self-complete ingest** — the worker keeps `ingest_requested_at` until `watch_complete` or a
   durable failure. Each cycle processes a small UTC-day chunk budget per target, then moves to the
   next cell. Enabled incomplete watches skip the current-island reconcile short-circuit so a single
   ingest queue keeps walking without extra `fill-gaps` calls.
3. **Heartbeat during ingest** — touch `market_data_worker` between targets and UTC-day chunks using
   wall-clock time. Operator health treats the worker stale after two configured ingest intervals
   plus slack, not the 5-second ingest-request poll.

Ops contract `thytrader-ops-contract-v30` advertises `catalog_health`:
`bounded_gap_inspection`, `ingest_self_complete`, `heartbeat_during_ingest`. Alembic `0043` is a
marker only (revises `0042` / USDC quote). `0044` remains reserved for research jobs (PR 95) and
`0045` for ledger P0 (PR 97).

## Consequences

- Large 1m watches no longer hang the API process; agents must treat `truncated` summaries as
  incomplete evidence, not as `watch_complete`.
- One ingest request can finish a lookback across cycles while other cells keep progressing.
- Long Coinbase walks cannot make overall health fail closed solely because one cell is fetching.

## Alternatives considered

- Raise the operator stale window only: rejected; a blocked cell would still starve the rest of the
  grid.
- Interpolate prefix holes to finish watches: rejected.
- Unbounded classification with a larger HTTP timeout: rejected; the API healthcheck still dies.
