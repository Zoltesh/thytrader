# Slice note: quantitative protection evidence (ADR 0112)

Worktree: `/home/hermes/projects/tt-review-protection` on `feat/review-protection`.
Does not bump the ops contract or regenerate `operator-report-v1.schema.json`. Lead owns that
integration, plus architecture index and roadmap.

## Behavior

`book_protection_status` no longer treats an arbitrary OPEN/PENDING protective order as cover.
Live cover requires a confirmed OPEN stop on the closing side, with remaining quantity and stop
geometry matching the book. Take-profit-only orders are unprotected on full and summary reads.
Pending and unknown stops are not confirmed cover. Attached-child presence does not bypass those
checks, and the same venue child is counted once. Paper remains `covered`, with evidence
`mechanism=synthetic`, `worker_dependent=true`, `venue_resting=false`, and null times.

Broker submission, `attached_entry_covers` as used by the execution loop, risk, and
`runtime_control` are unchanged.

## Interfaces

`ProtectionEvidenceResponse` (`extra=forbid`) on:

- `GET /api/v1/deployments/{id}` `positions[]` and compatibility `position` (`protection`)
- operator `strategies` / `runtime` `books[]` (`protection`, including coverage quantities;
  prices, cash, and order payloads still omitted)
- portfolio sleeve `books[]` (`protection`), including the manager briefing that uses the same
  projection

Fields: `required_quantity`, `covered_quantity`, `uncovered_quantity`, `stop_side`,
`stop_side_valid`, `stop_geometry_valid`, `mechanism` (`venue`/`synthetic`/`none`/`unverified`),
`venue_resting`, `worker_dependent`, `observed_at`, `verified_at` (null = unknown), `reasons`.

Legacy `protection_status` and `position_state` remain. UI badges use the evidence so a worker
stop, partial stop, or take-profit is not a green venue badge. Matching venue brackets stay
green.

## Ownership / merge

Edited `protection.py`, deployment position serialization, operator book model plus the
`_book_summaries` assignment, and portfolio `open_books`. The operator service and portfolio
view edits are the minimum wiring for those surfaces. Avoided `runtime_control`, execution
`service.py`, and `risk`. Other agents editing those files should not need this logic, but
`operator/service.py` `_book_summaries` and `portfolios/runtime_views.py` `open_books` are
shared and may conflict.

## Tests

- `tests/execution/test_protection_evidence.py` — ETH/ADA full brackets, summary OCO, stop-only,
  TP-only full and summary, pending/unknown, attached pending bypass, partial child fill,
  pyramid shortfall, duplicate child, stale bracket, wrong side, marketable stop, paper
  synthetic, API and sleeve payload equality
- `tests/execution/test_protection.py` — TP-only no longer covered; product isolation uses a
  matching stop-limit
- `tests/operator_diagnostics/test_deployment_books.py` — paper evidence is synthetic and prices
  stay omitted
- `web/src/lib/protection-evidence.spec.ts`
- `web/src/routes/deployments/[id]/detail.e2e.ts`
- `web/src/routes/portfolios.e2e.ts`

## Limitations

- Ops contract identity and the committed operator JSON schema are not updated here.
- Operator coverage quantities reveal book size. That is intentional and documented in the
  operator skill; prices and cash stay omitted.
- A confirmed stop that is more protective than the working stop but not equal to it is treated
  as a mismatch, not as cover.
- Paper verification time is always unknown. The report does not claim a worker poll time.
