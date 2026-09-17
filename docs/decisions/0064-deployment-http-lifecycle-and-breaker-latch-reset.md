# 0064: Deployment HTTP lifecycle fields and breaker latch reset

- Status: Accepted
- Date: 2026-09-17
- Relates to: [0058](0058-protection-lifecycle-accounting.md), [0060](0060-multi-book-deployment-api.md),
  [0065](0065-deployment-capital-accounting-http.md), [0019](0019-ops-contract-identity.md)

## Context

Ops-log issue **11 (runtime observability mismatch)** from the 17 Sep portfolio-research agent
run: skills and `thytrader-runtime show` promised ADR 0058 lifecycle, lease, and latch fields
that `GET /api/v1/deployments/{id}` did not return. Capital visibility was fixed separately in
[ADR 0065](0065-deployment-capital-accounting-http.md).

ADR 0058 shipped lifecycle commands, live capital columns, and durable daily-loss/drawdown
latches on deployments. Operator `DeploymentSummary` already reported
`lifecycle_command`, latches, `revision`, `worker_lease_held`, and live capital fields, and
skills directed agents to read capital from `thytrader-runtime show` /
`GET /api/v1/deployments/{id}`. The deployment HTTP response omitted lifecycle and latch
fields, so E2E agents could not verify latch state from the runtime lane alone. Capital now
lives in the `capital` block ([ADR 0065](0065-deployment-capital-accounting-http.md)); this
slice does not duplicate those fields at the top level.

Skills and operator messages also promised an explicit operator reset for latched breakers,
but no HTTP route or `thytrader-runtime` subcommand existed. In-app operator chat lacked
`operator_studies` and `operator_trade_reasons` read tools and could not pass `flatten` on
`runtime_stop`.

## Decision

1. **Deployment HTTP parity (ADR 0058 F12).** `DeploymentResponse` includes
   `lifecycle_command`, `daily_loss_latched`, `drawdown_latched`, `revision`, and
   `worker_lease_held` on list/show and after pause/resume/stop. Live capital stays in the
   nested `capital` block ([ADR 0065](0065-deployment-capital-accounting-http.md)).
2. **Explicit breaker latch reset.** `POST /api/v1/deployments/{id}/reset-breaker-latches`
   and `thytrader-runtime reset-breaker-latches UUID --confirm` clear latched breakers on one
   deployment. The mutation always requires `--confirm`; YOLO never skips it. HTTP 409 when no
   latch is set. Breaker-related `mismatch_detail` prefixes are cleared when present.
3. **Operator chat parity.** Add read-only `operator_studies` and `operator_trade_reasons`
   tools. `runtime_stop` accepts optional `flatten` as a query flag (default managed shutdown).
4. **Ops contract v26 / Alembic 0039.** Fan-out reserved label **ops v23** (this ADR). Bump to
   `thytrader-ops-contract-v26` with `expected_schema_revision` `0039` and advertise
   `breaker_latch_reset: ["paper", "live"]` on top of portfolio `deployment_capital_fields` and
   research v4 engines. Add Alembic `0039_ops_contract_v26_breaker_latch_reset` chaining from
   `0038` ([ADR 0066](0066-research-ops-contract-v4.md)). No DDL.

## Consequences

- Agents verify latch state from `thytrader-runtime show` and read live capital from
  `capital.allocated_capital` / `capital.venue_available_quote` without operator fallback.
- After a breaker trip, operators clear latches explicitly before resuming risk-increasing
  activity; resume alone is insufficient while latched.
- Stale Compose images missing `breaker_latch_reset` fail closed on agent CLI preflight until
  `make run`.
- Playbook sequencing remains unchanged (portfolio/research playbook shipped separately).

## Alternatives considered

- Operator-only capital/latch reads — rejected; runtime lane skills already documented the
  deployment HTTP surface.
- Auto-clear latches on resume — rejected; violates ADR 0058 explicit-reset semantics.
- Top-level `allocated_capital` / `venue_available_quote` — rejected; duplicates
  [ADR 0065](0065-deployment-capital-accounting-http.md).
