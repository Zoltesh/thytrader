# ADR 0111 slice completion: durable risk accounting scopes

Worktree `/home/hermes/projects/tt-review-risk`, branch `feat/review-risk`.
Resume finishes review notes against initial local commit `990bae48ff5a96d8a378583e9e0275c94d173a0b`.
Lead must integrate that commit **and** its follow-up commit; no history was rewritten.
No production mutation, credentials/dotenv read, production PostgreSQL, or service rebuild/restart.

## Delivered interfaces and behavior

- `daily_loss_snapshots` / `counts_for_daily_loss` (`risk/exposure.py`) load retained running,
  paused, and stopped books; `risk_bearing_snapshots` remains unchanged for occupancy.
- Worker `_risk_snapshots` and discretionary `_accounting_snapshots` retain stopped flat evidence.
  No execution-worker cycle/lifecycle changes beyond that snapshot helper and its import.
- Breakers retain UTC-day loss/latches through stop/replacement/deletion and scope drawdown to
  matching strategy or an actual discretionary product book. Deleted strategy FK null is not
  discretionary identity. Pinned-capital math, durable peaks, fills, fees, and snapshot bytes stay.
- `quote_scoped_snapshots` scopes admission/runtime capital and exposure to the proposed spot
  quote. Foreign quote inventory/reservations cannot enlarge the fractional loss denominator.
  Mixed/unsupported shared cash fails closed; mixed-quote portfolio comparisons and paper starting
  capital comparisons also deny instead of applying an invented conversion.
- New `risk/daily_accounting.py:flat_day_fill_pnl` uses exact per-product signed fill quantities and
  fee-inclusive cash changes. With no current-day baseline, no today fills on a flat old book is
  zero; current fills require flat midnight and current inventory. Overnight closure without
  opening marks, orphan fills, contradictory flat projections, and unapplied live economics are
  incomplete (`BREAKER_MARK_MISSING`), not guessed day PnL.
- `_marked_exposure` counts working entry remainders even under stale FLAT runtime overlays;
  normal position cost/reservation arithmetic is unchanged.
- `_pause_mode_running` now requires `product_id` and pauses only matching quote/mode running
  books. Stops and deliberate pauses remain intact. Discretionary admission excludes an unsaved
  candidate and retains one daily latch on a persisted peer when none already exists. It does not
  latch every historical book. Protective exits are not gated by these entry limits.
- Strategy deletion retains **all stopped paper/live books and referenced snapshots**, using FK
  detach. Existing active-book refusal stays. `counts.paper_deployments` counts removals (now 0);
  `live_deployments_kept` is unchanged. No new deletion payload field or shape.
- Optional policy fields (omitted from canonical bytes when unset): `max_order_quantity`,
  `max_order_notional_quote`, `min_available_quote_reserve`. Exposed by existing risk-policy HTTP
  PUT/GET and `thytrader-runtime set-risk-policy` flags; strict positive decimal-string validation.
  Reason codes `MAX_ORDER_QUANTITY`, `MAX_ORDER_NOTIONAL`, `BALANCE_RESERVE`. New monetary bounds
  require the declared policy quote. Compiled/default/legacy hashes do not change.
- Reserve is explicitly **notional admission headroom, not a guaranteed post-fill balance**.
  Live subtracts candidate notional/local unheld buys, not venue holds twice; ambiguous holds deny.
  Unknown live fees/slippage are not invented or guaranteed covered. Paper includes recorded cash
  losses/fees, working buys, and conservative stored/default taker fees. External concurrent trading
  is not atomically reserved. Quantity/notional/reserve only gate entries, never reductions.
- Explicit existing `reset-breaker-latches` remains deployment-local, confirmation-gated and
  audited at the existing HTTP boundary. It neither resumes nor erases underlying loss/capital.
- ADR 0111, security baseline, runtime skill/help, and narrow research deletion skill text updated.

## Migration and shared integration contracts

**`risk0065` is required**, superseding the initial note's no-migration decision. Isolated
PostgreSQL exposed that `ck_deployments_kind_identity` previously allowed only detached *live*
stopped strategy books. The migration widens that clause to paper too; no financial rows change.
Its predecessor is `0064` pending lead's reserved-lane rechain. Downgrade refuses while detached
paper evidence exists. Coordinated API/worker rollout must prevent old API deletion writers from
continuing to remove paper books.

Per resume instructions, this follow-up does **not** change global `OPS_CONTRACT_ID`,
`EXPECTED_SCHEMA_REVISION`, operator schema JSON, or the global ADR index. The initial local
commit already added one ADR 0111 entry to `docs/decisions/README.md`; it is preserved, not edited
again. Neither commit changes ops-contract constants or operator schema JSON. Lead owns the
release-head advertisement, landing/user/agent integration docs, architecture index, roadmap,
and operator risk-report echo/schema integration for the new optional fields/reason codes.

## Verification

Own `.venv` uses Python 3.14 and this checkout's editable package, not main's environment.
All non-DB pytest commands unset both integration database environment variables.

```text
env -u THYTRADER_TEST_DATABASE_URL -u THYTRADER_INTEGRATION_DATABASE_URL uv run pytest \
  tests/risk tests/execution tests/execution_worker tests/api/test_risk_policy.py \
  tests/api/test_risk_entry_bounds.py tests/api/test_discretionary_orders.py \
  tests/runtime_control tests/strategies -q --tb=short
881 passed, 15 skipped (DB suites covered separately), 12 upstream 422 deprecation warnings

THYTRADER_TEST_DATABASE_URL=<isolated-test-URL> env -u THYTRADER_INTEGRATION_DATABASE_URL \
  uv run pytest tests/persistence/test_risk_retention.py \
  tests/strategies/test_postgres_strategies.py \
  tests/persistence/test_postgres_performance_capital.py tests/persistence/test_postgres_risk.py \
  -q --tb=short
21 passed

uv run ruff check .
uv run ruff format --check .
uv run ty check
git diff --check
all pass (867 Python files formatted)

env -u THYTRADER_TEST_DATABASE_URL -u THYTRADER_INTEGRATION_DATABASE_URL \
  uv run pytest -q --tb=short
2818 passed, 92 skipped, 14 warnings, 1 expected shared-integration failure:
  tests/operator_diagnostics/test_ops_contract.py::test_expected_schema_revision_matches_alembic_head
  advertised 0064 != risk0065 (global constant intentionally reserved for lead)
```

PostgreSQL ran only in newly created `tt-risk-hermetic-pg-20261006` (`postgres:17-alpine`), loopback
port **55465**, with test-only credentials/data. Migrations ran with environment sanitized and
`Settings.model_config['env_file']=None`; scratch fixtures created unique throwaway DBs on that
server. Never port 5439. An initial `metadata.create_all` attempt failed on pre-existing NullType
schema stubs; using the actual migration chain fixed setup. All migration/deletion regressions
then passed, including unchanged pre/post-upgrade evidence, restart readers, late fills, explicit
reset, paper/live deletion retention, and guarded/lossless downgrade. No production DB tests.
The owned test container was stopped and automatically removed after verification.

Covered review cases: unrelated drawdown, stopped losses/latches, USD/USDC/USDT denominator
isolation, interleaved BTC/ETH fills/fees, overnight unknown versus recorded day-open equity,
UTC boundary with non-UTC observation timezone, daily rollover, legacy unknown live economics,
late fill before/after projection, paper deletion/snapshot retention, reset without implicit
resume/peak reset, old canonical policy compatibility, strict HTTP optional-bound validation,
local versus venue holds, recorded paper fees/loss, and unchanged risk-reducing supervision.

## GitNexus and review risk

- Initial read-only query/context/impact used `/home/hermes/projects/thytrader` as instructed;
  copied worktree storage was foreign. Main's graph was never mutated.
- Bootstrapped an owned local graph in `/tmp/tt-risk-graph`, registry name `tt-review-risk`, with
  `--workers 1 --embedding-threads 1 --index-only --pdg --embeddings`. Initial graph succeeded;
  its 50,000-node embedding safety cap skipped vectors, so a larger-cap rebuild was attempted.
- Incremental vector generation genuinely failed with duplicated `CodeEmbedding` primary key
  `:0`. A full forced/drop-embeddings retry was started; final outcome is recorded below.
- Upstream HIGH warnings reviewed for `persistence/schema.py` (79 import dependents),
  `_marked_exposure` (13 dependents: risk gates/portfolio exposure/briefing/sleeve views), and
  `evaluate_new_deployment` (10 dependents: strategy/discretionary/portfolio starts). Schema diff
  is one identity check; no performance-capital refactor. `risk_bearing_snapshots` was CRITICAL
  in initial impact and was not modified. UNKNOWN test-helper/variable callers were checked
  against source usages, not treated as unused.
- Local `detect-changes --scope all --limit 300` succeeded and reported HIGH risk, 16 files,
  43 changed symbols, 15 affected flows before staging added files. Complete staged/compare
  review is recorded below. Pretty CLI listings truncate display but not the result counts.
- Structural cycle check is complete and finds two cycles: execution_worker service↔venue,
  and web deployment-detail↔strategy-workspace. They are existing unchanged cycles; no new
  circular dependency was introduced. This is not claimed as a green zero-cycle check.

## Final graph outcome and merge cautions

Fresh owned PDG/FTS index succeeded: **82,998 nodes, 187,516 edges, 805 reported flows**.
`status` confirmed all 1,066 covered files match this checkout. Embeddings are **not clean**:
initial incremental generation failed with duplicate key `:0`; the slow full vector retry was
terminated only for this owned analyzer to bound resource/time use. Final rebuild used the
50,000-node safety cap and explicitly skipped vectors. The analyzer also warned that process
ranking/trace budgets omit whole flows and some cross-language property anchors are unresolved.
Do not read an absent flow/caller as an all-clear. No main graph was rebuilt.

Complete raw change review (not just pretty CLI output): staged/all **22 files, 104 symbols,
15 affected processes, HIGH**; compare against `901a059` **29 files, 191 symbols, 20 processes,
CRITICAL**. Both change-mapping responses had `partial=false`, `truncated=false`, no error.
This does not waive the analyzer's flow-budget limitations. Reviewed affected flows cover
entry gates, portfolio starts/sleeves, manager exposure briefing, and risk-policy publication /
canonical fingerprints; source and executed tests confirm the narrow behavior and legacy bytes.
Existing structural cycles remain the two noted above. Logs/raw JSON are under
`/tmp/tt-risk-graph/` (`final-pdg-analyze.log`, `detect-all-complete.json`,
`detect-compare-complete.json`).

Potential conflicts: loop/discretionary breaker-pause hunks with lifecycle lane; worker import and
`_risk_snapshots` with lifecycle/windows; one persistence identity constraint with other migrations;
shared runtime/research skill snippets. Interface notice is
`/tmp/tt-implementation/agents/risk-interface-note.md`. Lead must rechain risk0065, advertise the
integrated schema head, and pass the one currently failing release-contract check before release.
No UI was assigned or changed. No push, merge, deployment, policy publication, or reset was done.

## Integrated-test fixture follow-up after 7c64361

Lead integrated the slice at `53a3a0f` with a chain through `0068`. This follow-up changes only
tests and risk-scope clarification, not runtime risk, migrations, persistence schema, or global
contracts. No full suite or full graph rebuild was run; lead serializes integrated verification.

1. The retention test previously seeded schema `0064` through a current execution store and
   upgraded only to `risk0065`. That is not forward-safe when current stores require later
   `venue_observed_at` columns and fleet guards. It now reflects real `0064` tables, inserts
   explicit legacy columns directly, and captures lossless old-column row images (cash,
   quantities/prices/fees, baselines, latches, pinned capital, identities, timestamps, and
   canonical snapshot bytes). After migrating to **HEAD**, it asserts those exact old-column
   images are unchanged before invoking current deletion/restart/late-fill/reset services.
   No missing column, guard row, or current metadata is fabricated.
2. The isolated constraint tests remain on `0064 → risk0065 → 0064`, independent of future HEAD
   stores or unrelated downgrades. Seeded attached paper evidence round-trips losslessly and
   restores the actual old identity check. A separate raw-SQL detach proves widened paper
   identity and refuses rollback without changing any retained row.
3. Policy-scope decision: keep the existing conservative mixed-quote paper-funding refusal.
   `paper_capital_quote` is a single published envelope, not per-quote funding. Omitting occupied
   foreign-quote books would reuse that envelope independently for every quote; summing them
   would require absent FX evidence. Neither is an authorized interpretation. Stopped flat
   foreign evidence remains non-occupying. Regression cases cover USDC/USDT running/paused
   commitments against proposed USD funding, same-quote budget exhaustion, and stopped-flat
   non-occupancy.
4. The PostgreSQL portfolio scenario now receives its own freshly HEAD-migrated scratch DB.
   All start/sleeve funding/CAS/breaker/proposal/delete/orphan assertions stay intact. It neither
   clears shared financial evidence nor ignores a risk rejection nor skips the scenario.
   ADR 0111 and the runtime skill clarify the already-implemented scalar policy scope.

Exact focused verification against an owned disposable PostgreSQL 17 server on loopback 55465:

```text
env -u THYTRADER_TEST_DATABASE_URL -u THYTRADER_INTEGRATION_DATABASE_URL uv run pytest \
  tests/risk/test_accounting_regressions.py::test_paper_admission_cannot_sum_two_quote_currencies \
  tests/risk/test_accounting_regressions.py::test_stopped_flat_foreign_paper_evidence_does_not_consume_funding \
  tests/risk/test_accounting_regressions.py::test_same_quote_paper_funding_still_consumes_the_single_envelope \
  -q --tb=short
6 passed

env -u THYTRADER_INTEGRATION_DATABASE_URL THYTRADER_TEST_DATABASE_URL=<owned-test-server> \
  uv run pytest \
  tests/persistence/test_risk_retention.py::test_stop_delete_restart_reset_and_late_fill_keep_daily_evidence \
  tests/persistence/test_risk_retention.py::test_risk0065_downgrade_without_detached_paper_is_lossless \
  tests/persistence/test_risk_retention.py::test_risk0065_downgrade_refuses_detached_paper_evidence \
  tests/persistence/test_postgres_portfolio_runtime.py::test_deploy_supervise_propose_and_delete_against_postgres \
  -q --tb=short
5 passed

uv run ruff check <the three changed Python files>
uv run ruff format --check <the three changed Python files>
uv run ty check <the three changed Python files>
git diff --check
all pass
```

Local HEAD is still `risk0065`; **0068 integrated execution is lead's verification**, not claimed
here. Target graph impacts used the existing owned index: paper funding is HIGH (seven upstream
symbols, including deployment/portfolio starts), deliberately unchanged; test/fixture UNKNOWN
callers were confirmed by source/pytest references. Only a bounded, single-worker incremental
index refresh and change detection are used for this follow-up; existing graph/vector limitations
above are not waived. The owned test container is removed after checks. No production operations
or push; no shared root environment, database, or graph changes.

Follow-up graph limitation: the bounded incremental refresh returned success, but subsequent
`context` calls could not resolve `_seed_legacy` or the changed portfolio test. `detect_changes`
reported six files but only six symbols, and attributed hundreds of flows to the ADR section
(CRITICAL; latest raw response 769 flows, `partial=false`, `truncated=false`). That disagrees with
source and is **not a clean impact result**. Logs are `/tmp/tt-risk-seed-owned-context.json` and
`/tmp/tt-risk-integration-detect-complete.json`. No forced/full repair was launched under the
lead's concurrency instructions. Test collection/execution, typed checks, and the reviewed diff
are authoritative here; lead should repair/verify the integrated graph serially.
