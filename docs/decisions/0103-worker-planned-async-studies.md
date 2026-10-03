# 0103: Async studies are planned in the research worker

- Status: Accepted
- Date: 2026-10-03
- Amends: [0097](0097-runtime-parity-and-observability.md) (async submit planning)
- Relates to: [0092](0092-research-worker-pool.md), [0089](0089-agent-research-ergonomics.md)
- Ops contract: v63; Alembic remains 0060

## Context

Large asynchronous studies performed a complete preflight in the API and repeated planning
in the worker. Increasing the client's timeout hid the delay but left it on the API path.

## Decision

`POST /api/v1/research/studies?async=true` validates the start, snapshots every named strategy,
and pins datasets and evaluation bounds before persisting the existing durable job. It does not
call the study planner. The 202 continues to echo those exact bindings and the job identity.
Binding errors (invalid strategy, missing datasets, unavailable common bounds) still fail before
queueing. An accepted job means queued work, not a feasible or successful study.

The leased research worker derives/publishes candidates, plans and checks the larger async budget
(64 candidates, 512 windows), validates dataset bounds, and runs children. Planning rejection is
recorded as a failed job with `failed_phase: plan`, `error_code: study_window_rejected` or
`study_budget_exceeded`, and actionable `failed_detail`. Existing cancellation, lease fencing,
crash recovery, result fingerprints, and plan deduplication apply unchanged. Jobs already queued
use the same serialized fingerprint-bound request; no migration or new payload version is needed.

`plan-study` remains an explicit read-only preflight. Synchronous submits keep their smaller
8-candidate/128-window preflight and bounded wait, preserving their immediate 422 contract.
The API still derives candidates needed to resolve omitted common bounds; this input pinning is
bounded by the existing grid limit and avoids mutable strategies or newer datasets drifting while
jobs wait. Async client timeout remains 30 seconds, configurable through the existing flag.

## Alternatives

Moving input binding into the worker would change the 202 shape and allow inputs to drift after
submission. Storing another unresolved-request envelope would require queue compatibility and
migration work without helping this handoff's delay. Removing synchronous preflight would require
persisting its budget lane. These are outside this narrow change.
