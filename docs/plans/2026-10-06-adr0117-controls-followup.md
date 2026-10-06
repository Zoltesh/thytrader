# ADR 0117 controls — lead correctness follow-up

Follow-up to lead review of `5bfaa9a`. Initial implementation was integrated but **not
accepted/shipped**. This note supersedes its fail-open admission, offset completeness,
and separate command/receipt limitations. Work remains local to `tt-review-controls`;
lead alone integrates/releases/deploys. No production service, database, credentials,
policy, bot, or venue was operated.

## Six review blockers addressed

1. **Atomic revision confirmation.** Fleet commands no longer check an old inventory
   row and then invoke an unconfirmed re-read. `PostgresExecutionStore.
   record_confirmed_fleet_command` reads under `FOR UPDATE`, checks the exact confirmed
   revision/mode, and writes with revision CAS. `confirmed_command` reuses the existing
   lifecycle semantics and preserves all other runtime fields. A deliberate pause or
   lifecycle change wins when the preview is stale. A matching current command at the
   confirmed revision is `unchanged`, not evidence that this operation caused it.
2. **Cross-instance same-key serialization.** `PostgresFleetControlStore.operation_guard`
   holds a PostgreSQL session advisory lock through all progress transactions. Distinct
   store/engine instances cannot both act on an existing pending operation. Exceptions
   invalidate the session and await that cleanup even under repeated cancellation, rather
   than return a pooled connection holding a lock. Local serialization bounds reserved
   sessions; it is not the correctness lock.
   Progress writes also verify the guarded key. Fingerprints are SHA-256 of all confirmed
   inputs, allowing large target lists without overflowing the existing column.
3. **Latch receipt and confirmation fence.** `apply_latch` checks preview latch revisions
   under mode row locks and commits the update plus `latch_applied` receipt together.
   Every deliberate command advances revision, even an unchanged bit. Replaying a committed
   receipt never rewrites the latch. An uncommitted crash rolls back both effect/receipt;
   an intervening opposite command makes the old preview stale and refuses retry.
4. **Target receipt and restart.** `record_target` couples the real deployment mutation
   with its per-target result in one transaction. A crash before receipt rolls back that
   target; already committed targets survive. A crash after commit replays causal evidence,
   even if a worker/person later changes the book. Unknown exceptions remain pending,
   never accepted. Known persistence errors re-read the receipt before recording failure,
   so an ambiguous commit cannot clobber causal evidence. Mixed memory/PG backends are
   refused before any fleet effect.
5. **Fail-closed admission.** Missing latch table/row/read failures refuse starts and entry
   intent persistence. An unloaded process cache inhibits entry at boot/restart. Worker
   refresh failures inhibit only entry; protection, exits, and reconciliation remain
   callable. Test-only memory backends and `tests/conftest.py` explicitly initialize known
   memory admission; PostgreSQL fixtures execute real migration upgrades, not `create_all`.
   Admission linearizes at the transaction holding the mode latch row through start/intent
   insertion. Pre-disarm accepted intents/open orders can remain in flight: disarm is not
   cancellation and does not promise impossible instantaneous venue cancellation.
6. **Truthful inventory continuation.** Pages sort immutable `(created_at, UUID)` descending.
   `next_cursor` binds `as_of`, strategy filter, last key, and a membership/classification
   digest. Deletion or reclassification between reads returns 409 `inventory_changed`,
   not an offset skip. CLI/UI complete walks require unchanged metadata, unique ids,
   advancing cursors, and exact total count; no partial inventory is published. Legacy
   offset pages remain explicitly `complete:false`, even at an exhausted tail. Summary
   omission labels, explicit full show, and read-only orders/fills pages remain intact.

## Public interfaces for lead integration

- Existing inventory response adds `fingerprint: str`, `next_cursor: str | null`; existing
  route accepts `cursor` (offset 0; optional `as_of` must agree; strategy filter must agree).
  Default HTTP limit 50 is unchanged. The digest covers identity/created-at, strategy id/
  fingerprint, product, mode, and kind, not moving status/revision/valuation fields.
- CLI `list --limit N --cursor CURSOR` reads an explicit page. `list`/`list --all` follows
  checked cursors. Page mode has `complete:false`, `page_complete: bool`.
- Disarm/rearm body adds strict `expected_inhibition: {paper_revision?: int,
  live_revision?: int}`. Every scoped mode is required. Missing/stale expectations → 409;
  bool/string revisions → 422. Live rearm/flatten still require live acknowledgement
  (428); every mutation still requires `confirm:true`; CSRF/installation auth unchanged.
- CLI `--expect-inhibition paper:N` / `live:N` (repeat both for `--mode all`). No automatic
  read substitutes newer revisions for a person's consent. UI submits preview revisions,
  locks mode/preview while confirmation is pending, and disables actions until hydration.
- Operation `inhibition` is its **historical receipt snapshot**, not current latch proof.
  Read `fleet-status` / `GET /api/v1/fleet-control` for current state.
- Existing operation JSON gains strict `latch_applied`. This slice was never released;
  unreceipted/legacy documents lacking that field fail closed rather than infer what happened.
  Stop/flatten receipts mean a lifecycle command was recorded, not venue completion.

## Persistence / conflicts

No new DDL or sibling migration ID is needed: receipt progress uses existing operation JSON.
Only the unpublished migration `0067` docstring changes (fail closed before migration); its
revision/down_revision are untouched. Lead still rechains `0067` into `0065`/`0066`/`0068`.
No follow-up edits to global ops contract/schema revision/operator schema/ADR index.
The initial commit's global shared edits remain as recorded in the earlier completion note.

Shared merge touchpoints: `postgres_execution.py` adds the atomic command coordinator and
fail-closed latch read; `execution/memory.py` holds the memory latch through entry insert;
`tests/conftest.py` explicitly initializes/clears the process cache for hermetic tests.
No execution loop, protection, risk, historical fills/fees, published policy hashes, or strategy
snapshot changes. Browser fixture-only inventory metadata updated where the contract changed.

## Verification (final command results recorded before follow-up commit)

- Private PostgreSQL: container `tt-controls-followup-pg`, loopback port **25467**, 256 MB,
  database `controls_test`; per-module generated `controls_test_<uuid>` schema initialized
  through the complete real Alembic revision chain and dropped by fixture teardown.
  Never uses production port 5439. Tests validate loopback/test database before connecting.
- Focused Python command:
  `THYTRADER_TEST_DATABASE_URL=<private test URL on 25467> uv run pytest tests/fleet_control -q`
  → **40 passed**, including **15 PostgreSQL regressions**, no skips (one existing
  Starlette per-request-cookie deprecation warning).
- Wider affected Python:
  `uv run pytest tests/runtime_control tests/api/test_deployments.py
  tests/api/test_reference_deployments.py tests/execution tests/execution_worker tests/security -q`
  → **504 passed**.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check` → clean.
- Web `npm run check`, `npm run lint`, `npm run build` → clean/success.
- `npx vitest run src/lib/deployment-detail.spec.ts src/lib/fleet-control.spec.ts
  src/lib/inventory-fencing.spec.ts` → **51 passed**.
- Isolated browser command, from `web/`:
  `env -i PATH="$PATH" HOME="$HOME" LANG=C.UTF-8 npx playwright test
  --config=playwright.controls.config.ts` → **7 passed**. The committed test config uses
  unique test UI/API ports **24167/28167**, generated test installation auth/private temp
  credential directory, and a database/venue-free test API that never loads YAML or `.env`.
  Config refuses dotenv-bearing checkouts. Fleet mutations are browser-intercepted fixtures;
  no live API or venue is called. Playwright tears down both exclusively test-owned servers.
- GitNexus: reuse current local graph per lead serialization requirement, no rebuild of
  local/main graph. Pre-edit impact `execute_fleet` **HIGH** (four mutation routes), explicitly
  warned; store/admission/inventory hooks LOW. `detect-changes --scope all --repo .
  --limit 10000` completed: **42 files, 128 mapped symbols, 46 affected processes,
  CRITICAL aggregate risk**. Raw `LocalBackend.callTool('detect_changes', ...)` inspection
  confirmed no error/partial/truncated flags; CLI display itself caps printed detail.
  Risk is not waived: shared entry admission and the four mutation routes require lead's
  integrated release gate. The reused index cannot resolve newly added definitions until
  integrated reindex; source plus the executed regressions are authoritative for those.
  No local/main full graph rebuild or full repository suite was run in this follow-up.

## Remaining boundaries, not release-blocker deferrals

- Inventory loads membership rows in one database statement per page to compute its digest;
  it is not an atomic frozen valuation/ledger snapshot across requests. High churn produces
  an explicit incomplete/restart error, not a guessed complete fleet. Read caps still fail
  explicitly instead of publishing prefixes.
- Fleet control persists worker instructions only. Venue cancellation/flatten are asynchronous;
  no code here invokes a broker or claims atomic venue completion.
- PostgreSQL concurrency tests use independent engines/sessions (two API-store instances),
  not production processes or database resources. Crash regressions inject at the exact
  transaction/receipt boundaries and reconstruct new stores to prove restart behavior.
- Full repository suite/full graph rebuild intentionally left to lead's serialized integrated
  gate. No push, merge, deployment, or operational control is part of this follow-up.
