# 0131: Execution cycle timing

- Status: Accepted; §5's slow threshold amended by [0132](0132-execution-cycle-shared-reads-and-budget.md)
- Date: 2026-10-10
- Relates to: [0072](0072-catalog-health-bounded-gaps-self-complete-ingest.md),
  [0113](0113-deploy-anchored-window-cache.md),
  [0115](0115-durable-safety-alerts-and-supervision.md),
  [0130](0130-fleet-entry-health.md)

## Context

On 2026-10-10 the execution worker's cycle, nominally every 30 seconds, took about 2.5 minutes
in steady state and about 6.5 minutes after a restart. Operator `health` flapped to
`execution_worker` `HEARTBEAT_STALE` (heartbeat 95–160 s old) and recommended restarting the
worker, which would only empty its candle cache and slow the next cycle further. The worker
logged no cycle or per-book timing, so the cause was invisible: a slow cycle delays entries,
exits and protection checks for every book, and it degraded unnoticed except as a misleading
liveness flap. Standing instruction: nothing may degrade unnoticed.

## Decision

1. **Every cycle is measured.** `execution_worker.cycle_timing.CycleTimer` times the cycle's
   phases (`setup`, `portfolio_supervision`, `books`, `risk_snapshots` (the entry-gate evidence
   reload before and after each book), `safety_supervision`, `fleet_supervision`) and each
   visited book, keeping the ten slowest with deployment id, product, timeframe, status and
   mode. Measurement only observes: no phase or book does anything differently, and a failure
   to build or store a report never stops or alters a cycle.
2. **Venue calls are counted at one boundary.** The execution venue mounts
   `exchanges.request_timing.TimedHTTPAdapter` on the Coinbase SDK client's HTTP session, which
   market data, the broker and the balance reader share. Each request is recorded into the
   cycle's `observability.venue_calls.VenueCallLedger` (a context variable that
   `asyncio.to_thread` carries to the SDK's worker thread) as method, endpoint shape with ids
   replaced by `{id}`, outcome and latency. Parameters, bodies, headers and identifiers are
   never kept. Per phase and per book, the report carries request counts and venue seconds.
   SQLAlchemy cursor events on the worker's engine likewise record every SQL statement's
   count and latency (`observability.database_calls`), so time splits into venue, database and
   the remainder (CPU and waits).
3. **Cache warming is visible.** The deploy-window cache (ADR 0113) counts its range fetches and
   warming raises; the report carries the cycle's range requests, warming events, warming books
   and the retained windows and candles.
4. **Persisted per cycle.** Alembic 0075 adds `execution_cycles`: the worker inserts a row when a
   cycle starts and completes it with its duration and report JSON when it ends, keeping one
   day of rows. A running cycle is therefore visible before it finishes.
5. **Surfaced with a reason.** `health` and `runtime` gain an `execution_cycle` component:
   `CYCLE_SLOW` (degraded) when the newest completed cycle took longer than its interval or the
   running cycle has already overrun it, naming the slowest phase, venue traffic and slowest
   books; `CYCLE_WITHIN_INTERVAL` / `CYCLE_IN_PROGRESS` (healthy); `CYCLE_TIMING_MISSING` and
   `CYCLE_TIMING_UNAVAILABLE` (degraded: missing telemetry is never healthy).
   `health.payload.execution_cycle` is the compact summary; `runtime.payload.execution_cycle`
   is the full report plus the last 20 cycle durations. Every cycle is also logged
   (`execution_cycle_completed`, at warning level when slow).
6. **Heartbeat means progress.** The worker refreshes its heartbeat between books (at most
   every 10 seconds), not only at cycle start. `HEARTBEAT_STALE` then means a step has been
   stuck past the stale window or the worker is down; a slow but progressing cycle is
   `CYCLE_SLOW`, whose recommendation is to read the timing, not to restart.
7. Ops contract v91 adds `execution_cycle_timing` to `runtime_observability`; schema revision
   0075.

## Consequences

- An operator agent sees a slow cycle as `CYCLE_SLOW` with the slowest phase and books, and can
  attribute the time to venue calls, database work or cache warming from `runtime` alone.
- Two extra small writes per cycle (start and completion) and a bounded table (about 2,900 rows
  per day at a 30 s cadence).
- The timing adapter wraps only the execution worker's Coinbase client. The API, portfolio and
  market-data workers are not instrumented.

## Alternatives considered

- **Timing in logs only.** Rejected: logs are lost when Compose recreates a container, and an
  operator agent must not scrape logs.
- **A column on `worker_heartbeats`.** Rejected: one latest value cannot show a running cycle and
  the recent history together, and it would widen a table every worker shares.
- **Counting calls inside each adapter method.** Rejected: market data, broker and balance reads
  use different SDK entry points; the HTTP session is the one boundary they all cross.
