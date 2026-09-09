# 0012: Versioned operator diagnostics and confirmation-gated research CLI

- Status: Accepted
- Date: 2026-09-08

## Context

Agents need a supported way to diagnose a running ThyTrader instance and to perform bounded research mutations after a clone. Browser routes are not an agent contract. Shipping skill files before versioned commands exist would invite log scraping and PostgreSQL access.

## Decision

- Expose a versioned read-only operator surface: `GET /api/v1/operator/*` and `thytrader-operator`, both backed by the same application services.
- Every JSON report uses `schema_version` `thytrader-operator-report-v1`, UTC timestamps, overall `healthy|degraded|failed`, component reason codes, redaction metadata, partial-result warnings, and a recommended next action.
- CLI exit codes are `0` healthy, `1` degraded, `2` failed.
- Missing telemetry is never reported as healthy.
- Reports omit secrets, raw environment values, account identifiers, and balances.
- Ship `skills/thytrader-operator` against that contract.
- Research mutations stay on a separate confirmation-gated CLI (`thytrader-research` with `--confirm`) and skill. They may create drafts, publish immutable versions, and submit/list backtests. They must not deploy or trade.
- Research mutations append `research` audit events.

## Consequences

- Agents can diagnose without database credentials in the skill workflow (they invoke CLI/HTTP).
- Paper and live control remain out of the operator and research skills; they live on the separate `thytrader-runtime` skill (ADR 0013).
- The composable risk-policy registry is still unavailable; operator risk reports say so and only surface pause/mismatch findings.
- Paper/live performance is a fill-count slice until a dedicated ledger exists.

## Alternatives considered

- **Treat dashboard routes as the agent API:** rejected; they lack schema version, exit codes, and compatibility guarantees.
- **One skill that both diagnoses and deploys:** rejected; trading authority must not inherit from observation.
- **CLI that queries PostgreSQL as the documented agent interface:** rejected for the skill contract; stores remain an implementation detail of the CLI/API.
