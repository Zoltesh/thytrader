# ADR 0070: Mutation CLI installation auth wiring

## Status

Accepted

## Context

[ADR 0061](0061-application-trust-boundary.md) requires `Authorization: Bearer <installation-token>`
on HTTP mutations when `trust_boundary_enabled` is true. PR #79 wired installation auth only in
`thytrader-runtime` via `mutation_headers()`. The other confirmation-gated mutation lanes —
`thytrader-data`, `thytrader-research`, `thytrader-memory`, and YOLO skip audits through
`thytrader-agent-orchestration` — still called `request_json()` without installation headers.
Agents on production loopback installs therefore received HTTP 401 on data ingest, research
drafts/backtests, memory writes, and YOLO skip audits even when `thytrader-runtime` worked.

## Decision

1. Add `request_mutation_json()` to `thytrader.agent_http` as the shared loopback mutation helper.
   It loads `Settings()` when callers omit an explicit settings object and attaches
   `mutation_headers()` on every non-read request.
2. Resolve installation tokens for CLI mutations whenever one exists in
   `THYTRADER_INSTALLATION_TOKEN` or `$THYTRADER_CREDENTIALS_DIR/.installation-token`,
   **not** when local `trust_boundary_enabled` is true. Host agents run `development` while
   Compose APIs run `production`; gating on the CLI copy of `trust_boundary_enabled` left Bearer
   auth off even though the API required it.
3. Route all mutation-lane HTTP clients through that helper:
   - `data_control.client` watchlist, ingest, and fill-gaps continuation writes
   - `research.http` draft, publish, backtest, and study writes
   - `memory.client` journal, hook, notify, train, and trade-reason note writes
   - `agent_orchestration.client` skipped-confirmation audits
   - `runtime_control.client` (refactored to the same helper; behavior unchanged)
4. Document in lane skills that every mutation CLI sends installation auth the same way as runtime.
   Browser CSRF remains browser-only; CLIs never send CSRF.

## Consequences

- Production agents can complete data → research → memory workflows without bypassing the trust
  boundary or scraping logs for workarounds.
- Read-only operator and diagnostic CLIs stay on `request_json()` without installation headers.
- No Alembic or ops-contract bump: HTTP contract and middleware are unchanged; only CLI wiring
  catches up to ADR 0061.
- Future mutation CLIs must call `request_mutation_json()` (or an equivalent wrapper) rather than
  raw `request_json()` for writes. The dedicated `fill-gaps` POST originally shipped on
  `request_json()` and 401'd on production loopback; it now uses the same helper as ingest.

## Alternatives considered

- **Per-client `mutation_headers(settings)` only in runtime:** rejected; left other lanes broken.
- **Server-side trust-boundary bypass for loopback User-Agent:** rejected; weakens ADR 0061.
- **Implicit auth inside `request_json()` for all non-GET methods:** rejected; operator reads and
  health probes must stay unauthenticated when the boundary is off.
