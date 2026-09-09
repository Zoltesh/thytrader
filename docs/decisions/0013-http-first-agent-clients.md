# 0013: HTTP-first agent CLIs and confirmation-gated runtime control

- Status: Accepted
- Date: 2026-09-09

## Context

ADR 0012 shipped versioned operator diagnostics and a confirmation-gated research CLI. The Phase 6 exit gate still required those workflows to work without database access. The store-backed CLIs needed `THYTRADER_DATABASE_URL`, which is not an agent contract.

Paper and live HTTP control already existed for the browser Deploy tab. Agents still lacked a separate, confirmation-gated command surface. Folding that authority into operator or research skills would treat trading as an extension of observation.

## Decision

- Default `thytrader-operator` and `thytrader-research` to the loopback HTTP API (`THYTRADER_API_BASE_URL` or `http://{api_host}:{api_port}`). `--local` remains an explicit store-backed path. HTTP failures must not fall back to PostgreSQL.
- Agent HTTP clients may only target loopback (`127.0.0.1`, `localhost`, `::1`).
- Add read-only `GET /api/v1/operator/runtime` and `thytrader-operator runtime` for paper/live **status** without cash or order payloads.
- Commit JSON Schema `skills/thytrader-operator/references/operator-report-v1.schema.json` and `thytrader-operator schema-check`.
- Ship a third skill and CLI, `thytrader-runtime`, for start/pause/resume/stop against `/api/v1/deployments`. Mutations require `--confirm`. Live start also requires `--i-understand-live`.
- Record runtime control as `runtime` audit events without cash, quantities, or secrets.
- Keep product skill files in `skills/`. Add Cursor auto-discovery pointers under `.cursor/skills/` that defer to those files.

## Consequences

- Diagnose and research automation can run with only the loopback API, matching the Phase 6 exit gate.
- Paper/live control is justified as a distinct confirmation-gated surface because the browser already exposes those mutations and agents otherwise invent unsafe shortcuts.
- Operator and research skills still have no trading authority.
- Live arming remains an explicit dual-flag action.

## Alternatives considered

- **Keep Postgres-backed CLIs as the documented agent interface:** rejected; skills must not require database credentials.
- **Silent HTTP-to-Postgres fallback:** rejected; it hides API outages and mixes two contracts.
- **One skill that diagnoses and deploys:** rejected; trading authority must not inherit from observation or research.
- **New runtime HTTP API besides `/api/v1/deployments`:** rejected; reuse the existing browser contract and add confirmation at the CLI/skill boundary.
