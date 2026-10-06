# Safety alerts slice completion (ADR 0115)

Worktree: `feat/review-alerts`. Lead integrates. Do not treat this note as a production deploy.

**Historical initial slice note:** `d4e3897` was integrated but not accepted for release.
The lead identified false-recovery and unfenced-write defects. The follow-up safety corrections
and exact current verification are in [2026-10-06-safety-alerts-review-followup.md](2026-10-06-safety-alerts-review-followup.md).

Resume session 2026-10-06: verified the preserved partial work, fixed three failing tests,
cleaned all Ruff/ty/format findings, and re-ran every gate. Committed locally on
`feat/review-alerts`; lead alone integrates, pushes, merges, and deploys.

## Interfaces

- Alembic `0066_durable_operator_alerts` (`down_revision = 0064` in this checkout; re-point if
  `0065` exists at merge). Table `operator_alerts`. Partial unique index
  `ux_operator_alerts_open` on `(code, subject) WHERE resolved_at IS NULL`; check constraints
  pin severity, delivery status, and positive occurrences.
- `EXPECTED_SCHEMA_REVISION` is `0066` so the existing head check passes. Ops contract id was
  already left at `thytrader-ops-contract-v66` (kept as found). Lead resolves release contracts.
- Settings (env, defaults): `THYTRADER_ALERT_CONSECUTIVE_FAILURE_CYCLES=3`,
  `THYTRADER_ALERT_DECISION_MISSED_BARS=2`, `THYTRADER_ALERT_DELIVERY_MAX_ATTEMPTS=5` —
  each `ge=1, le=100` (`Field` bounds in `config.Settings`). Not added to the YAML settings
  allowlist (runtime lane owns that surface).
- Worker: `run_execution_worker(..., alert_service=)` and `_run_cycle(..., alert_service=,
  worker_interval_seconds=)`. CLI constructs `PostgresAlertStore` plus
  `ReloadingNotificationSender`. Supervision never resumes books and does not submit orders.
- Read API: `GET /api/v1/operator/alerts` → `AlertsReport` (`report_kind=alerts`).
- CLI: `uv run thytrader-operator alerts` (HTTP default, `--local` supported).
- UI: `/alerts` in the System nav. Banner when `delivery_warning` is set.
- Codes: `BOOK_PAUSED_MISMATCH`, `BREAKER_LATCHED`, `STOP_UNCOVERED`,
  `STOP_COVERAGE_UNKNOWN`, `STOP_TRIGGERED_UNFILLED`, `DECISION_DEADLINE_MISSED`,
  `MAINTENANCE_DEADLINE_MISSED`, `WORKER_LEASE_STALE`, `WORKER_BOOK_FAILURES`.
- `ActorOrigin.SYSTEM` exists so runtime alert payloads are not labeled human or agent.
  Memory CLI origin flags remain human|agent.

## Behavior verified by tests

- Repeat cycles increment one open row; recovery resolves it; a later finding opens a new row.
- `notify_provider=none` stores `skipped` with an explicit delivery-disabled detail and does
  not invent a webhook.
- A raising sender is recorded as failed, retried under the attempt cap, then not retried.
  A successful delivery is not repeated on later cycles.
- The same in-memory store survives a new `AlertService` ("restart") without a second open row.
- `_run_cycle` records a mismatch alert when `_process_one` is a no-op, so supervision does
  not depend on a successful signal evaluation.
- Three consecutive `RuntimeError` cycles pause a running book with
  `WORKER_CONSECUTIVE_FAILURES` and the next cycle still calls `_process_one`.
- A user pause (`STOP_NEW_ENTRIES` plus mismatch) is not resumed or overwritten.
- 6h book inside the 120s settling grace does not get `DECISION_DEADLINE_MISSED`.
- Unknown lease age is an alert and is not described as process death. Occupied + unknown
  lease is also `MAINTENANCE_DEADLINE_MISSED`. A fresh lease is not.
- HTTP `GET /api/v1/operator/alerts` returns the critical open alert and the delivery warning.
  Without a store, storage is `unavailable` and the open list is empty.
- A live stop that traded through unfilled voids verified cover when it was the only resting
  closing-side stop: both `STOP_UNCOVERED` and `STOP_TRIGGERED_UNFILLED` fire, and supervision
  still does not escalate to market orders. Paper books keep synthetic cover.

## Resume-session fixes (2026-10-06)

- `execution_worker/service.py`: `_pause_repeatedly_failing_books` used `UUID` at runtime
  while it was imported only under `TYPE_CHECKING` (`NameError` on the pause path); moved
  `from uuid import UUID` to a runtime import. Also narrowed `_may_pause_for_failures` to a
  `Deployment` argument with explicit `None` handling at the call site.
- `execution_worker/service.py`: the handler now binds the exception and feeds
  `WORKER_BOOK_FAILURES`. **Correction:** Python 3.14 supports parenthesis-free exception
  lists; the base repository syntax was valid. Adding `as error` required parentheses.
- `alerts/supervision.py`: triggered-stop detection now runs once per snapshot
  (`_trigger_consumed_orders` returning order id → latest close) and feeds both
  `_stop_trigger_findings` and the new `_cover_voided_by_triggered_stops` check, so a live
  book whose only closing-side stops all triggered unfilled also raises `STOP_UNCOVERED`
  (test expectation of the preserved partial work that the implementation did not meet yet).
- `alerts/supervision.py`: `_lease_stale_finding` had an unused `now` parameter; removed.
- Type hygiene: moved `AlertApplication`/`AlertService`/`AlertStore`/`OperatorAlert`/
  `datetime`/`Decimal` imports into `TYPE_CHECKING` where annotation-only; kept runtime
  imports that FastAPI/Pydantic evaluate (`AlertStore` in `api/routes/operator.py` with
  `noqa: TC001`, `UUID` in `alerts/report.py` because `AlertItem.id` is a Pydantic field).
- Test helpers rewritten from `dict[str, object]` overrides to explicit typed keyword
  construction (repo pattern), removing ~60 `ty` diagnostics; sender test doubles are typed
  against the `AlertDeliverySender` protocol; docstrings added to every public test.
- `persistence/schema.py`: `__all__` re-sorted (`operator_alerts` before `order_intents`).

## Verification (this worktree, all hermetic)

- Initial command `uv run pytest -q -x --ignore=tests/api/test_strategy_backtest_integration.py`
  → **2781 passed, 88 skipped** (PostgreSQL integration tests skip without
  `THYTRADER_TEST_DATABASE_URL`; no production database was used).
- `uv run ruff check .` → clean. `uv run ruff format --check .` → clean.
- `uv run ty check` → clean.
- Web: `npx vitest run` → 46 files / **433 tests passed**; `svelte-check` → 0 errors,
  0 warnings; `prettier --check .` + `eslint .` → clean; `npm run build` → succeeds.
- GitNexus: the copied `.gitnexus` storage identified the main checkout (foreign), so the
  stale copy was moved to `/tmp/tt-implementation/alerts-stale-gitnexus/` (untracked files
  only; main's own index untouched) and a fresh index was built in this worktree:
  `GITNEXUS_STORAGE_PATH=/tmp/tt-implementation/alerts-gitnexus node .gitnexus/run.cjs
  analyze --name tt-review-alerts --workers 1 --embedding-threads 1 --index-only --pdg
  --embeddings` (236s; the tool skipped embeddings at its 50,000-node cap — impact and
  detect-changes do not need them).
- `detect-changes --scope all --repo tt-review-alerts`: 25 files, 53 symbols, 29 affected
  processes, overall risk **critical** — driven by `create_app` (new `alert_store` wiring)
  and the worker cycle, i.e. the assigned integration surface itself. Targeted upstream
  impact for the symbols edited in the resume session is LOW: `gather_safety_findings`,
  `_run_cycle`, `run_execution_worker`, `build_alerts_report`, `check_operator_schema`
  (all `risk: LOW`). Full suites above cover the changed flows; lead re-checks integrated.

## Limitations

- PostgreSQL upsert/restart coverage is implemented (`PostgresAlertStore`) but not executed
  here unless `THYTRADER_TEST_DATABASE_URL` is set. No production database was used.
- YAML `set-settings` does not yet expose the three thresholds (env-only; lead/runtime lane).
- Delivery exhaustion is reported as status `exhausted` when attempts reach the configured
  max; the stored row status remains `failed`.
- Initial unfenced supervision pause was a release-blocking defect, **not** an acceptable
  limitation. The follow-up now acquires the lease, re-reads, and revision-fences the write.
- `0066` parent is `0064` only because this worktree has no `0065`.
- The initial Postgres adapter had no executed integration evidence. The follow-up runs
  dedicated migration/repository tests on a private disposable database and aligns resolved
  feed ordering with the in-memory store.

## Lead merge notes

- Avoid colliding with the watched-market freshness slice: this change adds `/alerts` to
  workstation chrome and `alerts` to operator report kinds. Freshness files were not edited
  except shared operator/docs lists that had to gain the new kind.
- Shared release contracts touched and left for lead: `EXPECTED_SCHEMA_REVISION` → `0066`,
  `OPS_CONTRACT_ID` kept at `thytrader-ops-contract-v66` as found,
  `tests/operator_diagnostics/test_ops_contract.py` revision assertion, migration `0066`
  head, `ActorOrigin.SYSTEM` addition, and the already-present `alerts` kind added to
  `skills/thytrader-operator/references/operator-report-v1.schema.json` (kept, not edited
  further in this session). No other global schema JSON edits were made.
- Reindex after integration. Do not deploy from this worktree.
