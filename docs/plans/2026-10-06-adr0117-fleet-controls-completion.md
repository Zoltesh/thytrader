# ADR 0117 slice completion — inventory truth and fleet controls

Worktree: `/home/hermes/projects/tt-review-controls` branch `feat/review-controls`.
GitNexus impact used the copied index via `--repo /home/hermes/projects/thytrader`
(read-only). That graph resolves the original checkout; this worktree did not mutate it.

## Interfaces

- `GET /api/v1/deployments?limit&offset&strategy_id&as_of`
  - Order `created_at_desc_id_desc`. Fields added: `has_more`, `total`, `order`, `as_of`.
  - Default limit remains 50. `has_more` is exact for the pinned snapshot.
- `GET /api/v1/deployments/{id}?detail=summary|full`
  - Summary sets `ledger_omission`, `historical_orders_included=false`,
    `historical_fills_included=false`. Full clears the omission label.
- Existing `GET /orders` and `GET /fills` are now reachable as
  `thytrader-runtime orders|fills UUID [--limit N] [--cursor C]`.
- `GET /api/v1/fleet-control` reads the latch.
- `GET /api/v1/fleet-control/preview?action&mode` is read-only.
- `POST /api/v1/fleet-control/{disarm|stop|flatten|rearm}` requires trust-boundary auth,
  `confirm: true`, and `i_understand_live: true` for live rearm and live-capable flatten.
  Body is strict. Idempotency key is required.
- CLI: `list` defaults to a complete snapshot; `--limit/--offset` is one honest page;
  `--all` is the explicit complete walk. `show --detail summary|full`.
  Fleet commands are hard-gated `--confirm`. YOLO does not cover them.

## Entry latch hook

`entries_allowed` consults `thytrader.execution.entry_latch` after the worker calls
`refresh_process_entry_inhibition` at the start of `_run_cycle`. That is the narrow loop
integration. `loop.py`, `protection.py`, and risk modules were not edited.

Durable backstop: `PostgresExecutionStore.create_deployment` and entry `save_intent` lock
`fleet_entry_inhibition` in the same transaction. In-memory stores use the bound fleet gate.
Non-entry intents are not refused. A missing latch table keeps the previous admit behavior
so this code can start before lead applies the rechained migration. A present table with a
missing mode row fails closed.

## Migration

`alembic/versions/0067_fleet_entry_inhibition.py` currently revises `0064`. Lead must set
`down_revision` to `0066` after alerts `0066` and risk `0065`. `EXPECTED_SCHEMA_REVISION`
is `0067` in this worktree so the head test passes. Ops contract id was not bumped.

## Limitations

- Recorded stop/flatten commands are asynchronous worker effects. The API does not claim
  orders cancelled or positions flat, and it does not wrap venue calls in one transaction.
- A worker cycle that already loaded a clear latch can still attempt one entry until the
  next refresh. The intent insert then refuses. That refusal can surface as a closed-bar
  error for that cycle; exits on later cycles are not blocked.
- Fleet preview loads every deployment row. It is complete relative to `list_deployments()`
  without a limit, not a silent 50-row page.
- PostgreSQL integration tests are skipped unless `THYTRADER_TEST_DATABASE_URL` points at a
  disposable database. They were not run against production.

## Lead merge notes

- Rechain 0067 and reconcile `EXPECTED_SCHEMA_REVISION` / ops contract capabilities.
- Do not remove the `entries_allowed` cache check or the `save_intent` entry refusal; they
  are the coordinated hook.
- UI lives at `web/src/lib/FleetControls.svelte` on `/deployments`.

## Resume session (2026-10-06 afternoon) — fixes and verification

Continued from the preserved worktree. No new features were needed; this session fixed
latent defects and ran the full verification gate.

### Defects fixed

- `tests/fleet_control/test_http_and_cli.py`: the CSRF test fetched
  `GET /api/v1/security/session` without the installation credential, which the trust
  boundary requires. It now sends `Bearer fleet-token` and asserts 200.
- `tests/fleet_control/test_inventory_and_controls.py` and `test_http_and_cli.py`: the
  process latch snapshot leaked across tests because disarm loads it globally. The
  HTTP test clears the cache after asserting the start refusal, and the snapshot test
  clears before its first assertion.
- `tests/runtime_control/test_cli.py`: the stale-ops-contract test patched
  `cli.list_deployments`, which the inventory dispatch moved to
  `cli.run_inventory_read`; patch target updated (`assert_not_called` still proves the
  gate fires first).
- `src/thytrader/fleet_control/store.py`: removed dead `with_targets`, which called an
  unimported `replace` (ty F821) under a `type: ignore` — a latent `NameError` on first
  call. It had no callers.
- `src/thytrader/execution/entry_latch.py`: replaced the `global` statement with a
  module-level `_LatchCache` holder (ruff PLW0603). Behavior is identical.
- `src/thytrader/api/app.py`: extracted `_resolve_fleet_control_store` so `create_app`
  and `lifespan` are back under the complexity limit (ruff C901). No behavior change.
- `src/thytrader/fleet_control/serialization.py` and
  `src/thytrader/runtime_control/client.py`: stored JSON objects are now validated into
  strict `dict[str, object]` at the boundary (`_mapping` / `_strict_mapping`), and
  inventory rows return validated `list[object]` (one narrow `cast` with reason in
  `_rows_of`, mirroring the existing `_list` helper).
- `src/thytrader/fleet_control/store.py`: the `FleetControlStore` protocol now declares
  `operation_guard -> asyncio.Lock`, matching both implementations and fixing ty
  protocol-compatibility errors at every call site.
- `src/thytrader/fleet_control/postgres.py`: `_snapshot_from_rows` accepts
  `Sequence[RowMapping]` (SQLAlchemy returns a sequence, not a list).
- `web/src/lib/FleetControls.svelte`: fixed a svelte-check nullable-closure error with
  `{@const}` and an eslint unused-parameter error by replacing a no-op `.filter` with an
  explicit derived condition. Prettier formatting applied to the three touched files.
- `src/thytrader/persistence/schema.py`: `__all__` entries re-sorted (ruff RUF022).

### Verification results

- `uv run pytest tests/` → **2778 passed, 89 skipped** (PostgreSQL-gated; hermetic, no
  production DB), after the fixes.
- `uv run ruff check .` → clean. `uv run ruff format --check .` → clean.
- `uv run ty check` → clean (15 diagnostics resolved, no suppressions added).
- Web: `npx vitest run` → **435 passed (46 files)** including `fleet-control.spec.ts`;
  `npm run check` (svelte-check) → 0 errors; `npm run lint` (prettier + eslint) → clean;
  `npm run build` → success. Ran in this worktree after `npm ci` (its own `node_modules`).
- GitNexus: the copied worktree index was foreign (its registry resolves the main
  checkout), so it was moved aside to `.gitnexus.copied.bak` and a fresh local graph was
  built with `--name tt-review-controls --workers 1 --embedding-threads 1 --index-only
  --pdg --embeddings` (419 s, 83,963 nodes / 189,382 edges, at base commit `901a059`).
  `detect-changes --scope all --repo .` → clean (no partial/truncated flag); changed
  symbols are exactly this slice's files. Upstream impact: `entries_allowed` and
  `process_entry_inhibited` both LOW risk, epistemic exact.

### Shared-file edits to reconcile (unchanged from the first pass)

- `src/thytrader/ops_contract.py`: `EXPECTED_SCHEMA_REVISION` `0064` → `0067` (with the
  matching assertion in `tests/operator_diagnostics/test_ops_contract.py`). Ops contract
  id not bumped; lead owns the release contract.
- `docs/decisions/README.md`: one ADR 0117 index paragraph at the top.
- `alembic/versions/0067_fleet_entry_inhibition.py` revises `0064`; lead rechaining to
  `0066` after alerts/risk migrations land.
- `src/thytrader/execution/lifecycle.py`, `src/thytrader/execution_worker/service.py`,
  `src/thytrader/execution/memory.py`, `src/thytrader/persistence/postgres_execution.py`,
  `src/thytrader/persistence/schema.py`: the coordinated entry-latch hook only.
