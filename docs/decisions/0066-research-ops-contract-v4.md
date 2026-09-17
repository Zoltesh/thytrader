# 0066: Research ops-contract v25 and bar-backtest-v4 advertisement

- Status: Accepted
- Date: 2026-09-17
- Relates to: [0019](0019-ops-contract-identity.md), [0062](0062-research-paper-semantics-audit-stage-4.md),
  [0052](0052-richer-sweep-axes-study-catalog.md)

## Context

[ADR 0062](0062-research-paper-semantics-audit-stage-4.md) shipped `thytrader-bar-backtest-v4` in
code, skills, and the research CLI engine-support matrix, but the compiled ops contract still
advertised only v1–v3 through `thytrader-ops-contract-v22` / Alembic `0035`. Agents following the
portfolio+research playbook therefore:

- saw a matching `/health/ready` ops contract on images that could not accept v4 `submit-backtest`
  requests;
- received HTTP 422 enumerating only v1–v3 without the shared stale-image rebuild hint; and
- could not distinguish a healthy pre-0062 image from the current research checkout.

This slice does not change v4 kernel semantics ([ADR 0062](0062-research-paper-semantics-audit-stage-4.md))
or study-catalog persistence ([ADR 0052](0052-richer-sweep-axes-study-catalog.md)).

## Decision

1. Add `thytrader-bar-backtest-v4` to the compiled `backtest_engines` list.
2. Bump `OPS_CONTRACT_ID` to `thytrader-ops-contract-v25` and `expected_schema_revision` to `0038`.
3. Add Alembic `0038_research_ops_contract_v25` as a no-op marker revision (revises `0037` reserved
   for the portfolio slice).
4. Extend `thytrader-research` stale-engine detection: HTTP 422 bodies that list v1–v3 but omit v4
   receive the shared `make run` rebuild hint, matching the existing v1/v2-only path.

Default `submit-backtest` engine remains v1 for fingerprint stability; skills and the portfolio
playbook continue to instruct agents to name v4 explicitly for new maker research.

## Consequences

- Healthy images missing v4 fail the ops-contract preflight instead of accepting research commands
  that will 422.
- Contributor and operator docs list v4 in the ops-contract table; `ops/` stays operator-only.
- Rebuild is required after merge so `/health/ready` and agent CLIs agree on v25.

## Alternatives considered

- **Bump package `__version__` instead:** rejected; [ADR 0019](0019-ops-contract-identity.md) keeps
  content identity separate from `0.1.0`.
- **Silently default `submit-backtest` to v4:** rejected; would change immutable run fingerprints
  for agents that omit `engine_contract_version`.
