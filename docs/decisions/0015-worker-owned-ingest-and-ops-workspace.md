# 0015: Worker-owned ingest, inclusive Coinbase paging, and the ops workspace

- Status: Accepted
- Date: 2026-09-11

## Context

ADR 0014 made the dedicated market-data worker the Parquet publication path and required one-shot
ingest to share `ingest_once`. The API process still called that publisher on `POST /api/v1/data/ingest`.
Compose mounts the API dataset volume `:ro`, so that call could not publish. Operating agents then
patched Python, Compose, or Coinbase paging instead of queueing a worker job.

Coinbase candle `end` is inclusive. Non-overlapping exclusive pages dropped the oldest bar on a full
350-bar 5m page.

Operator health treated a configured `THYTRADER_DATABASE_URL` as healthy without pinging PostgreSQL,
and treated Docker `/tmp` readiness files as worker liveness. Those files are process-local.

Contributor `AGENTS.md` and GitNexus workflow live at the git root. Operating a running instance is
a different job: skills, confirmation flags, and `make run` — not source edits.

## Decision

- `POST /api/v1/data/ingest` returns 202 and sets `ingest_requested_at` on the watchlist. It does not
  call `ingest_once`. The market-data worker is the only caller of `ingest_once`. The API dataset
  volume stays read-only.
- `thytrader-data ingest` / `fill-gaps` poll `GET /api/v1/data/ingest` until the worker clears
  `ingest_requested_at` after `ingest_once`, or 45 minutes elapse.
- Coinbase historical paging uses an inclusive page `end` (last closed start, cap 350) and advances
  `page_start` to `inclusive_end + duration`.
- Operator health pings the API's SQLAlchemy engine when a database URL is set. Worker health prefers
  PostgreSQL `worker_heartbeats` rows. Readiness files remain a unit-test fallback when no heartbeat
  store is attached.
- Ship `ops/` as the Cursor workspace for operating a running instance. Product skills stay in
  `skills/`; `ops/.cursor/skills/` symlinks to them. Operating agents must not edit `src/`, Compose,
  Dockerfiles, Alembic, or tests. Rebuild with `make run` only when the user asks or when a ready
  listener is missing agent routes, mismatches the CLI version, or mismatches the ops contract.

## Consequences

- Agents can ingest 5m research data without weakening the API `:ro` mount.
- Stale Compose images fail closed with an explicit `make run` hint instead of a bare HTTP 404.
- Migration `0016` is required before heartbeats and ingest flags persist.
- ADR 0014's "share `ingest_once`" alternative remains: one publisher function, worker-only caller.

## Alternatives considered

- **Make the API dataset volume writable:** rejected; publication belongs to the supervised worker.
- **Keep exclusive Coinbase `end` and drop the oldest bar:** rejected; complete-only ingest cannot
  invent or skip closed bars.
- **Treat Docker healthcheck files as operator health:** rejected; they are not cross-process liveness.
- **Teach operating agents GitNexus + `src/` at the git root:** rejected; operating and contributing
  are different workspaces.
