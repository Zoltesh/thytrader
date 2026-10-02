# 0092: Research worker pool — research compute leaves the API process

- Status: Accepted
- Date: 2026-10-02
- Amends: [0073](0073-durable-research-jobs.md) (the API no longer admits or runs jobs, and
  restart no longer re-queues every running row), [0088](0088-portfolio-model-and-portfolio-backtest.md)
  (portfolio backtests run in the research worker; `max_concurrent_portfolio_backtests` is gone),
  and [0089](0089-agent-research-ergonomics.md) (a synchronous submit is a bounded wait on a
  queued job)
- Relates to: [0002](0002-modular-monolith.md), [0015](0015-worker-owned-ingest-and-ops-workspace.md),
  [0019](0019-ops-contract-identity.md), [0030](0030-agent-e2e-primary-surface.md),
  [0083](0083-unified-backtest-model.md), [0090](0090-research-correctness-optional-take-profit-diagnostics.md),
  [0091](0091-portfolio-deployment-limits-and-manager-proposals.md) (deployed portfolios' sleeve
  bots are runtime, not research; only portfolio backtests use the pool)

## Context

Backtests, composed studies (OOS, walk-forward, sweep, WFO, cross-market), and portfolio
backtests are CPU-bound `Decimal` simulations. They ran inside the API process: the lifespan
started `ResearchJobRunner` and `PortfolioBacktestRunner`, and a synchronous submit ran the
simulation inside the request. `asyncio.to_thread` did not help much: the pure-Python kernel
holds the GIL, so the event loop got 5 ms slices between simulation steps.

Measured on the running stack on 2026-10-02: with research load the API container sat at 100%
of one core; one 1d backtest round trip took 46 s while a WFO study and a three-job batch ran;
UI and agent polls queued behind research; the synchronous `submit-study` CLI (5 s timeout)
timed out. Concurrency was two compiled constants (`MAX_CONCURRENT_RESEARCH_JOBS = 2`, defined
twice, and `MAX_CONCURRENT_PORTFOLIO_BACKTESTS = 1`) enforced by counting running rows and
advertised in the ops contract. The reference host has 8 GB of RAM and no swap.

## Decision

1. **A dedicated `research-worker` Compose service is the only place research runs.**
   `thytrader-research-worker` starts a light supervisor (stdlib plus settings, ~35 MB) that
   keeps `THYTRADER_RESEARCH_WORKER_COUNT` worker *processes* alive (default 2, memory-safe on
   8 GB), spawned with the `spawn` start method. Each process runs one job at a time, so the
   worker count is the single bound on concurrent research of every kind. The existing `worker`
   service was not reused: it holds Coinbase credentials and takes portfolio snapshots, and
   research needs neither credentials nor exchange access. The research worker gets no Coinbase
   credentials and mounts the dataset volume read-only.
2. **Durable claiming on the existing job tables.** `research_jobs` (backtests, studies) and
   `portfolio_backtest_jobs` feed one pool; no second queue technology. A claim is one
   `UPDATE … WHERE job_id = (SELECT … FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING` that sets
   `status = running`, `lease_owner = <host>/<supervisor pid>/<slot>/<nonce>`,
   `lease_expires_at = now() + lease` (database clock), and `attempts + 1`. Workers alternate
   which table they try first. A heartbeat thread with its own event loop and engine renews the
   lease six times per lease period (default lease 60 s), so a long simulation step cannot starve
   it. A running row whose lease expired — or that never had one, such as a row the pre-0057 API
   was running — is swept: cancelled when cancellation was requested, failed with
   `research_worker_lost` once `attempts` reaches `THYTRADER_RESEARCH_JOB_MAX_ATTEMPTS` (3),
   otherwise re-queued. A replacement process for the same supervisor slot re-queues its dead
   predecessor's rows at once instead of waiting for the lease.
3. **Fenced writes.** Every research-job execution write requires `status = running` and the
   worker's own `lease_owner`, and raises `ResearchJobLeaseLostError` otherwise, so a worker whose
   lease lapsed can never overwrite the attempt that replaced it. Portfolio job writes check the
   lease immediately before each store call (the portfolio store owns its transaction; results
   are content-addressed, so the one-statement window can at worst journal a duplicate entry).
4. **Recycling and shutdown.** A process exits cleanly after `THYTRADER_RESEARCH_WORKER_MAX_JOBS`
   (50) jobs or when its RSS grows `THYTRADER_RESEARCH_WORKER_MAX_RSS_GROWTH_MB` (256) above the
   RSS measured after its first job; the supervisor replaces it at once, and backs off
   exponentially (1 s to 30 s) after crashes. On SIGTERM a worker hands its running job back to
   the queue (the attempt is not counted) and exits without waiting for a simulation thread.
   Workers raise their `oom_score_adj` to 800 (Compose sets the container to 500) so the kernel
   kills research, whose job is re-queued, before the API, PostgreSQL, or host services, and they
   ask the kernel to signal them if the supervisor dies.
5. **The API stays thin.** It validates, binds datasets, plans within the right budget, inserts a
   job row, and never executes research. `?async=true` answers 202 as before. A synchronous
   submit long-polls the job row for at most `THYTRADER_RESEARCH_SYNC_WAIT_SECONDS` (25 s):
   completed answers the same 201 body as before; failed maps the new `research_jobs.error_code`
   back to the same 422 (`backtest_window_rejected`, `study_window_rejected`,
   `study_budget_exceeded`) or 503; still queued or running answers 202 with the job and
   `sync_wait_seconds`. Without PostgreSQL there is no queue, so research submissions answer 503.
   `create_app(research_execution=ResearchExecutionMode.IN_PROCESS)` keeps an in-process harness
   for tests only; a server never runs it.
6. **Results are unchanged.** The worker composes the same PostgreSQL services as the API did, so
   the same request yields the same `run_fingerprint`, `result_fingerprint`, separately stored
   `diagnostics_json` (ADR 0090), study, and plan fingerprints. Tests run the same requests
   through the in-process harness and the worker and compare the HTTP bodies byte for byte.
7. **Health, contract, and CLI.** Operator health (`thytrader-operator health`,
   `GET /api/v1/operator/health`) gains a `research_worker` component and `payload.research_workers`
   (configured and live workers; each worker's state, job, jobs completed, RSS, and heartbeat age;
   queued and running counts and the oldest queued age per queue and combined). Ops contract v52 /
   Alembic 0057 drops `max_concurrent_research_jobs` and `max_concurrent_portfolio_backtests` — the
   bound is deployment configuration that health reports — and adds `research_worker_pool`
   capabilities. `thytrader-research list-research-jobs --strategy-id` lists a strategy's jobs.

## Consequences

- Research load no longer slows API, UI, or agent requests: the API process does no simulation.
- Memory: a worker is ~95 MB idle and ~150 MB at peak on a 4,000-bar 1h backtest; with the
  supervisor the service is ~225 MB idle and ~335 MB at peak for two workers, while research's
  working memory leaves the API.
- A synchronous submit is a research job too: it is listed, counted in queue depth, and
  cancellable. With no live research worker it waits its bound, then answers 202 `queued`, and
  health reports `RESEARCH_WORKER_MISSING`.
- Throughput is bounded by the worker count; extra jobs wait `queued` in FIFO order.
- The research CLI allows 60 s for a synchronous submit and prints the job with `next_action`
  on a 202; the UI polls a 202 to completion.

## Alternatives considered

- **More threads in the API.** The simulation holds the GIL; isolation is impossible.
- **A `ProcessPoolExecutor` inside the API.** Still the API's container, memory budget, and
  lifecycle; services and engines do not pickle; a crash disrupts the API.
- **Reuse the `worker` service.** It holds Coinbase credentials and mixes failure domains.
- **A broker queue (Redis, Celery, RQ).** A second queue technology and more memory for nothing
  `SKIP LOCKED` on the existing tables does not already give.
- **`LISTEN/NOTIFY` pickup.** Lower latency than 0.5 s polling, more moving parts; deferred.

## Deferred

- The HTTP signal-trace route (ADR 0090) still re-evaluates a trace on the API's thread pool.
- A hard RSS ceiling that kills a worker mid-job, and container memory limits.
- Queue priorities or per-kind lanes, `LISTEN/NOTIFY` pickup, and a queue position on job records.
