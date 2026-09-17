# ADR 0073: Durable bounded research jobs

## Status

Accepted (2026-09-17)

## Context

QA follow-up found five research-job defects:

1. WFO `submit-study` falsely failed after the CLI's fixed 30-second HTTP timeout while the API
   continued work for many minutes with no returned job identity.
2. Equivalent WFO plans with different `evaluation_end` bounds but identical effective windows
   persisted duplicate studies.
3. Async backtest jobs were tracked only in an in-memory dictionary and were lost on API restart.
4. Large async result `GET` responses were unbounded and kept consuming CPU after client disconnect.
5. WFO `plan-study` returned every child window in one JSON payload.

ADR 0069 added HTTP 202 async backtests and bounded study readback, but jobs remained
process-local and composed studies still blocked synchronously.

## Decision

1. **Durable PostgreSQL `research_jobs`** — backtests and composed studies queue through one table
   with `queued`, `running`, `completed`, `failed`, `cancelled`, and `expired` statuses. The API
   admits at most two concurrent jobs, expires jobs after 24 hours, supports cancellation, reports
   progress, and requeues `running` rows on restart.

2. **Async composed studies** — `POST /api/v1/research/studies?async=true` returns HTTP 202 with a
   `job_id`. Poll `GET /api/v1/research/jobs/{job_id}` (backtests also keep
   `GET /api/v1/backtests/jobs/{job_id}`). Cancel with
   `POST /api/v1/research/jobs/{job_id}/cancel`.

3. **Plan-fingerprint dedupe** — `plan_fingerprint` hashes the effective child window schedule.
   Submit reuses an existing persisted study when the plan matches, even when request bounds differ.

4. **Bounded backtest reads** — `GET /api/v1/backtests/{result_fingerprint}` defaults to
   `detail=summary`. `detail=full` returns trades and equity but checks client disconnect before
   responding.

5. **Compact planner output** — `POST /api/v1/research/studies/plan` defaults to a summary document
   (`window_count`, `fold_count`, fingerprints, warnings). Pass `detail=full` for child windows.

## Consequences

- Long WFO and 1m research should queue with `--async` and poll `show-research-job` instead of
  holding one HTTP request open.
- Ops contract `thytrader-ops-contract-v31` advertises research job statuses, concurrency, and expiry.
  Alembic revision `0044` creates `research_jobs` and adds `plan_fingerprint` to
  `published_research_studies`.
