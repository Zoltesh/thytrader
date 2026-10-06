# ADR 0110 slice completion

Date: 2026-10-06. Worktree/branch: `feat/review-lifecycle` at
`/home/hermes/projects/tt-review-lifecycle`. Finished implementation and local commit only;
lead integrates, pushes, merges, and deploys. No running services, live mutation tools,
production database, `.env`, credentials, operational policy, or bot state were touched.
Python checks used this worktree's own Python 3.14 `.venv`.

## Interfaces and behavior

- `reconcile_open_orders` retains its signature. Initial fill replay, every watched order,
  and attached children are supervised under preexisting `PAUSED`/`STOPPED` state. The
  first newly observed fault is retained; an operator pause is never resumed. Read failures
  become unknown state and a fixed, redacted reconciliation detail, not rejection or proof
  of venue absence. Healthy siblings still reconcile. Attached child identity is persisted
  as unknown before the first read. Canceled orders and partial applied fragments remain
  watched for later fills.
- `execution/stopped.py::supervise_stopped_deployment` is the stopped worker entry point.
  It reconciles before any flat decision, then maintains or flattens every owned product,
  including discretionary books and a sole secondary position. Missing immutable strategy
  rules leave an explicit fault and stored protection supervision, not a new paused state.
  Managed shutdown continues protection and cancels entries; explicit flatten remains a
  separate lifecycle instruction. Idle product runtime phases settle from observed inventory
  and order state, not from the primary book alone.
- `flatten_stopped_residual` delegates to `flatten_residual_book`. The latter is also usable
  without a strategy (`flatten_discretionary_residual`). `_marketable_exit` observes live
  cancel/fill races before replacement and blocks another cover while filled protection or
  unapplied fills remain unresolved. Unknown cancel remains supervised across restart.
- `FLATTEN_AWAITING_EXECUTABLE_CONTEXT` is the existing-string-field pending reason when
  no verified executable price exists. Protection is retained; only confirmed entry-purpose
  orders may be canceled on the missing-context path. A genuine existing fault is not hidden
  by this wait. A pending-price state can settle when later real protective fills prove flat.
- `load_verified_exit_context` permits only matching, enabled products with positive finite
  venue constraints and the **most-recent closed, positive-volume traded** candle. A provider
  preview can supply it when anchored history is unavailable. Old, in-progress, disabled,
  and synthesized no-trade contexts cannot authorize cancellation of protection.
- Empty decision windows journal `data_gap`, pause only a running book, reconcile live fills,
  and maintain native protection from stored stop/target levels when fresh provider evidence
  exists. No signal/ATR evaluation, decision cursor advancement, or risk-increasing submit
  occurs on incomplete history. Missing discretionary windows also reconcile.
- Shared `market_data/window_state.py::WindowCacheWarmingError` distinguishes bounded cold
  cache rebuilds from genuinely absent venue candles. Strategy decision/HTF/extra-clock and
  discretionary loaders catch it and continue reconciliation/no-entry protection without
  pausing, resuming deliberate pauses, or advancing the decision cursor. Windows lane now
  imports this exact type; its copy of the new file is byte-identical.
- Essential adjacent repair in `fill_ledger.py`: atomic stores call the shared projector on a
  full snapshot, so it now selects `order.product_id` itself. A full exit returns no focused
  position while keeping siblings in `positions[]`, avoiding reinserting a sibling into the
  closed product. Missing-metadata entry faults keep `STOPPED` durably. Historical applied
  fills, fees, published policy hashes, immutable strategy snapshots, and deployment IDs are
  unchanged. No persistence schema or SQL implementation changes.

## Tests and checks

All pytest commands explicitly removed `THYTRADER_TEST_DATABASE_URL` and
`THYTRADER_INTEGRATION_DATABASE_URL`; fixtures also block external network and checkout dotenv.
No test used production port 5439 or any production database URL.

```bash
env -u THYTRADER_TEST_DATABASE_URL -u THYTRADER_INTEGRATION_DATABASE_URL \
  uv run pytest tests/execution/test_adr_0110_stopped_lifecycle.py \
  tests/execution_worker/test_missing_decision_candles.py -q
# 43 passed

env -u THYTRADER_TEST_DATABASE_URL -u THYTRADER_INTEGRATION_DATABASE_URL \
  uv run pytest tests/execution tests/execution_worker -q
# 442 passed

env -u THYTRADER_TEST_DATABASE_URL -u THYTRADER_INTEGRATION_DATABASE_URL \
  uv run pytest -q
# 2807 passed, 89 skipped, 2 warnings (144.95s)

env -u THYTRADER_TEST_DATABASE_URL -u THYTRADER_INTEGRATION_DATABASE_URL \
  uv run pytest tests/operator_diagnostics/test_contributor_ops_docs_gate.py \
  tests/operator_diagnostics/test_skill_compatibility.py \
  tests/operator_diagnostics/test_ops_contract.py -q
# 18 passed, existing Alembic warning only

uv run ruff check .              # passed
uv run ruff format --check .     # passed: 865 files
uv run ty check                 # passed
git diff --check                # passed
```

The 89 full-suite skips are unconfigured PostgreSQL integration coverage. Warnings are the
existing Starlette HTTP-422 deprecation and Alembic `path_separator` deprecation. Hermetic
regressions cover paused sibling/child faults, initial replay faults, missing-price protection,
preview freshness, discretionary and multiple/secondary books, cancellation-fill races,
terminal late fills, partial fragments, unresolved fills, restart, missing snapshot dispatch,
and cold warming on decision/HTF/extra/discretionary clocks. Existing live fixture entries
already projected into inventory are now correctly stamped applied; script cancellation state
is coherent. Signal-exit expectations allow a cover immediately after confirmed cancellation,
without assuming an unnecessary extra poll.

## GitNexus review

- The copied `.gitnexus` reports foreign storage. Initial query/context/upstream impact used
  `/home/hermes/projects/thytrader` read-only (root index rebuilt at `ca97364`; sibling HEAD
  warning verified against source). Main's index was never rebuilt or modified.
- Local graph storage is isolated at `/tmp/tt-implementation/lifecycle-gitnexus`, registered as
  `tt-review-lifecycle`. Rebuilds used `--workers 1 --embedding-threads 1 --index-only --pdg
  --embeddings`. Final structural/PDG repair succeeded: 83,315 nodes / 188,547 edges / 799 flows;
  status reported covered content up-to-date.
- An attempted uncapped `--embeddings 0` rebuild genuinely failed with duplicate
  `CodeEmbedding` primary key `:0`. Repair used `--force --drop-embeddings` and the normal cap;
  embeddings were explicitly skipped above 50,000 nodes. This is **not** a successful semantic
  embedding rebuild. Logs: `/tmp/tt-implementation/lifecycle-gitnexus-final-analyze.log` and
  `lifecycle-gitnexus-repair.log`. Lead must retain this tool limitation in integration review.
- Local `detect-changes --scope all --limit 1000 --repo .` reviewed the staged change including
  new files: MEDIUM risk, three affected atomic fill projection flows. No partial/truncated
  result caveat was emitted; CLI display itself shows only the first 15 symbols.
  Final local upstream impact: reconciliation MEDIUM (34 impacted symbols, eight direct
  callers including ordinary worker/discretionary paths); shared projector LOW (16 symbols,
  three direct callers in fill application). API shapes, acknowledgement gates, and policy
  admission were not changed. Analyzer process enumeration still reports its normal global
  depth/branch caps and unresolved cross-language axes; source and executed tests are the
  authority, not absent graph flows.

## Integration ownership and limitations

- Preserve the **already-present** single ADR 0110 row in `docs/decisions/README.md`; resume
  made no further global index edits. No changes to `OPS_CONTRACT_ID`,
  `EXPECTED_SCHEMA_REVISION`, global operator schema JSON, architecture index, or roadmap.
  No migrations. Lead owns shared release/landing docs and operator schema/contract integration.
- Runtime skill text is a narrow appended ADR 0110 section. Public payload shapes and CLI flags
  are unchanged; new diagnostics use existing `mismatch_detail` and existing `data_gap` rows.
- Merge overlap is `execution_worker/service.py` imports and lifecycle helpers, plus appended
  runtime skill text and the preserved ADR index row. `_run_cycle` and `_closed_window_for`
  bodies were not edited. `protection.py` was not touched. Lead should merge the byte-identical
  `window_state.py` addition once, preserving the windows lane's import rather than a second
  independently defined exception class.
- Ancillary projector ownership was communicated in
  `/tmp/tt-implementation/agents/lifecycle-ledger-interface-note.md`; warming coordination is
  in `lifecycle-interface-note.md`.
- With no fresh price at all, retain native protection and explicit pending/fault state. There
  is no top-of-book substitution or fabricated market candle. Synthetic trails, paper matching,
  and signal/time exits require actual sufficient market data. Missing projection metadata is
  unknown inventory and stays faulted/protected, never advertised as successful flatten.
- Canceled orders remain observed because the broker contract supplies no final fill-visibility
  watermark. This adds read work on long-lived canceled history. PostgreSQL integration tests
  were not executed in this slice; the shared pure projection and in-memory atomic path are
  covered, and lead should run isolated PostgreSQL suites on integration.
