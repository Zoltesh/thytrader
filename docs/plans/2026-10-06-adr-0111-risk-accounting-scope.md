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
