# ADR 0069: Async backtest jobs and bounded study readback

## Status

Accepted (2026-09-17)

## Context

Agent portfolio research on 1m datasets routinely exceeds gateway timeouts (~420s) when
submitting several template backtests synchronously. Study `GET` responses also embedded every
child window and stitched equity point (~437 KB for one WFO), which is unsafe for operator agents.

Parameter sweeps and WFO already cap Cartesian grids at eight candidates, but OpenAPI only
documented per-axis limits. Evaluation-window rejection messages used half-open semantics that did
not match operator expectations when `evaluation_end` equaled the stated maximum.

## Decision

1. **Async backtests** — `POST /api/v1/backtests?async=true` returns HTTP 202 with a `job_id`.
   Poll `GET /api/v1/backtests/jobs/{job_id}` for `queued`, `running`, `completed`, or `failed`
   status and immutable fingerprints when complete. Jobs are tracked in-process for the current API
   instance (no new PostgreSQL table in this slice).

2. **Study summary projection** — `GET /api/v1/research/studies/{study_fingerprint}` defaults to a
   summary document without child `windows` or stitched equity `points`. Pass `detail=full` for the
   canonical persisted study.

3. **Parameter grid validation** — reject Cartesian products above eight candidates at request
   validation with an explicit cross-field error. Document the limit on `parameter_axes` in OpenAPI.

4. **Evaluation boundaries** — centralize dataset fit checks on half-open
   `[evaluation_start, evaluation_end)` semantics. The latest allowed `evaluation_end` is the last
   complete candle open minus one decision-clock bar; that bound is inclusive.

## Consequences

- Long 1m research batches should queue async jobs and poll instead of holding one HTTP request
  open.
- Operator and research agents should prefer default study `GET` and only request `detail=full`
  when child windows are required.
- Ops contract v27 advertises async job statuses; Alembic revision `0040` is a marker only
  (revises `0039` from ADR 0064 breaker-latch reset).
