# Slice completion: quantitative protection evidence (ADR 0112)

Worktree `/home/hermes/projects/tt-review-protection`, branch `feat/review-protection`.
Initial local slice commit: `729883a`. This resume addresses all five lead review findings.
Lead alone integrates, pushes, merges and deploys. No production/API mutations, running-service
changes, credentials, `.env` reads, database connections, Docker resources, or `make run` were used.
Python uses this worktree's own `.venv` and editable source.

## Implemented behavior

- TP-only exits, a STOP/BRACKET intent on a plain limit or marketable order, and stray trigger
  fields on those non-stop kinds cannot establish venue stop cover. Bounded unlabelled stop
  kinds still work without intent rows.
- Live cover requires actual venue identity, closing side, executable stop geometry matching the
  book, and enough **remaining** quantity. Partial fills and pyramid adds expose the shortfall.
- Geometry uses the working target, not entry. Profitable long/short trailing stops can cross
  entry. Stop-only limits validate a positive limit at/through their trigger on the closing side;
  missing geometry stays unknown. `geometry_basis` discloses exactly what was checked.
- Identity folding precedes active-status filtering. A newer canceled/filled/rejected/unknown
  observation of the same venue child cannot resurrect an older OPEN row. Equal-time conflicting
  observations or unorderable timestamps stay unverified. Duplicates never sum partial rows;
  tied matching rows use the smaller remainder. Parent-child aliases use the same identity.
- `Order` does not guarantee that an OPEN row has a venue id or that `updated_at` came from a
  successful reconciliation. No-id OPEN rows are unverified. `observed_at` is explicitly a local
  row update; `verified_at` is **always null** rather than inventing venue verification.
- Local-row recency uses an aware reporting clock and an explicit 120-second bound (four default
  30-second worker polls, **not** strategy candle frequency). Stale, naive/undated or future-dated
  rows cannot contribute covered quantity. This reports, but never pauses/resumes or changes
  supervision. Custom slower polling can report unverified without changing broker behavior.
- Matching recent ETH/ADA full brackets retain legacy `covered` as a qualified persisted-state
  claim. Side/geometry validity is separate from confirmation and quantity: pending matching
  geometry can be valid but contribute zero covered quantity.
- Paper remains `covered` / `open_protected`, explicitly `synthetic`, `worker_dependent=true`,
  `venue_resting=false`, with unknown observation/verification times.
- UI table badges, KPI protection sentences, bot rows and portfolio sleeve books no longer show
  green for paper, partial, TP-only, stale, locally-observed-only, or missing legacy evidence.
  Recent local matching geometry says **Unverified**, not fresh venue cover. Exiting remains
  distinct. Render-time age checks also prevent old/future/invalid verification payloads from
  staying green.

No changes to broker submission, reconciliation, risk, lifecycle helpers, execution worker,
`execution/service.py`, or the execution loop's `attached_entry_covers` decision. No migration.

## Public interfaces for lead integration

Strict (`extra=forbid`, strict types, constrained nonnegative decimal strings)
`ProtectionEvidenceResponse` appears on:

- deployment detail/full/summary/list `positions[]`, plus compatibility `position`;
- operator `strategies` and `runtime` `books[]` (coverage quantities only; still no prices/cash/orders);
- portfolio deployment sleeve `books[]` and manager briefing projection.

Fields:

- `required_quantity`, `covered_quantity`, `uncovered_quantity` (exact decimal strings);
- `stop_side` (`buy`/`sell`/null), `stop_side_valid`, `stop_geometry_valid`;
- `mechanism` (`venue`/`synthetic`/`none`/`unverified`), `venue_resting`, `worker_dependent`;
- `observed_at`, `verified_at` (null means unknown; never infer verification from local writes);
- **resume additions**: `observation_source` (`persisted_order`/`synthetic_worker`/`none`),
  `freshness` (`recent_local`/`stale`/`unknown`), `evaluated_at`, `freshness_max_age_seconds` (120),
  `geometry_basis` (`working_target`/`stop_limit_trigger`/`unknown`);
- `reasons` (stable codes listed in `PROTECTION_REASONS`). New review codes include
  `stop_geometry_unknown`, `unsupported_stop_kind`, `venue_identity_missing`,
  `local_observation_only`, `local_evidence_stale`, `observation_time_unknown`,
  `observation_time_future`.

Legacy `protection_status` and `position_state` remain. `book_protection_evidence(..., now=...)`
accepts an optional deterministic reporting clock. `book_position_state(..., evidence=...)`
lets serializers reuse the same classification instead of checking the age twice at a boundary.
TypeScript `ProtectionEvidence` mirrors the exact enums; shared `protectionBadge` drives badges.

## Verification

All database environment variables below were explicitly removed for broad Python runs. Fixtures
stay in-memory; production PostgreSQL (including port 5439) was never used.

- `env -u THYTRADER_TEST_DATABASE_URL -u THYTRADER_INTEGRATION_DATABASE_URL uv run pytest
  tests/execution/test_protection.py tests/execution/test_protection_evidence.py
  tests/execution/test_protection_review.py tests/execution/test_position_state.py
  tests/execution/test_take_profit_none.py tests/operator_diagnostics/test_deployment_books.py
  tests/api/test_book_marks_api.py -q`: **104 passed** (final retry, 11.22s). A redundant preceding
  rerun hit the harness's 90s timeout during shared-machine load; it is not counted as a pass.
- Same environment guard, `uv run pytest tests/execution
  tests/operator_diagnostics/test_deployment_books.py tests/api/test_book_marks_api.py
  tests/api/test_deployments.py -q`: **430 passed**.
- Same environment guard, `uv run pytest -q`: **2806 passed, 89 skipped, 2 warnings**. Skips are
  database-dependent tests without configured isolated test URLs; no PostgreSQL verification claim.
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: passed (863 files).
- `uv run ty check`: passed.
- `cd web && npm run lint`: passed.
- `cd web && npm run check`: 0 errors, 0 warnings.
- `cd web && npm run test:unit -- --run`: **46 files, 436 tests passed**.
- `cd web && npm run build`: passed.
- `npm run test:e2e -- --config .protection.playwright.config.ts
  'src/routes/deployments/\[id\]/detail.e2e.ts' 'src/routes/portfolios.e2e.ts'`: **55 passed**.
  Temporary config extended the checked-in Playwright config with unique test-only loopback ports
  API 18442 / UI 14442, `reuseExistingServer=false`, empty Coinbase fields/database URL, and API
  launch `uv run python -c 'from thytrader.api.app import create_app; from thytrader.config import
  Settings; import uvicorn; uvicorn.run(create_app(Settings(_env_file=None)), host="127.0.0.1",
  port=18442)'`. UI proxy matched that API. This avoids reading `.env` and reusing another agent's
  services; config was removed afterward. No real live mutation endpoint was called.

Regression coverage includes both profitable trailing directions (paper/live), newer terminal and
unknown duplicate rows in either tuple order, equal-time status conflicts, newer partial fills,
parent-child aliases, missing venue identity, local staleness/naive/future times, strict payload
validation, invalid STOP-labelled order kinds on full/unlabelled summaries, and stop-limit limits.
API/portfolio/operator tests preserve evidence serialization and omission of prices/cash in operator
books. TS/e2e tests cover synthetic/TP-only/local-only/missing evidence and KPI/portfolio wording.

## GitNexus and structural review

Initial copied index was foreign. Query/context/upstream impact used
`--repo /home/hermes/projects/thytrader` **read-only**; main's graph was not rebuilt or modified.
`book_protection_status` upstream risk was **CRITICAL** (13 symbols; deployment serializers,
operator books, portfolio/manager reads). The UI `protectionText` impact was LOW; verified its
Svelte caller in source because graph callers did not include it.

Moved only the local foreign copy out of this worktree, bootstrapped a new locally-owned
`tt-review-protection` graph, workers=1, index-only, PDG. The first `--embeddings` hit the 50k safety
cap. Retrying full `--embeddings 0 --embedding-threads 1` failed in GitNexus 1.6.12:
`CodeEmbedding` duplicate primary key `:0`. This is a genuine graph-tool error, **not** clean
embedding verification. Repaired the interrupted graph with local `--drop-embeddings --pdg
--index-only --workers 1`, then refreshed code/PDG after final source changes. Lead must retry
embeddings on the integrated graph; this slice does not claim embeddings complete. A final
incremental refresh also exited 1 before completing its writable-set load (no explicit error
line), leaving an incomplete index. Its zero-flow detect result was rejected, not reported as
safe. A sequential full `--force --drop-embeddings --workers 1 --index-only --pdg` rebuild then
exited 0 and `status` reported **up-to-date**. Final local detect checks again identify the expected
26 affected reporting flows at CRITICAL risk, rather than the incomplete zero.

Local `detect-changes --scope all --repo . --limit 10000` and full-slice
`detect-changes --scope compare --base-ref 901a059 --repo . --limit 10000` review deployment,
operator and portfolio projections and the UI readers; the full-slice risk remains CRITICAL,
expected for this public reporting change. `check --cycles --json --repo .` reports two complete
enumerated cycles, **identical to the main read-only baseline**: execution_worker service↔venue
and deployment-detail↔strategy-workspace. Neither cycle is introduced here. No broad cycle refactor.

## Ownership, shared contracts, and limitations

- Lead must integrate ops contract capability/version, generated global operator JSON schema,
  operator landing docs/agent integration, architecture index and roadmap. Neither
  `OPS_CONTRACT_ID`, `EXPECTED_SCHEMA_REVISION`, nor global operator schema JSON changed here.
- The initial preserved commit already amended ADR 0058/0098 status lines, added the ADR 0112
  entry in `docs/decisions/README.md`, and updated operator `references/report-schemas.md`.
  Those existing shared changes are retained; the resume did not edit the global ADR index or
  generated operator schemas further.
- Minimal shared wiring in `operator/service.py::_book_summaries`, API deployment position
  serialization, and `portfolios/runtime_views.py::open_books` may conflict with other lanes.
  Lead should keep the evidence assignment and reuse it for state; no unrelated service edits.
- A more protective but non-equal working stop is a mismatch, not cover. Stop-limit coverage does
  not guarantee a fill through gaps or prove current market price; geometry basis is explicit.
- There is no persisted dedicated venue-verification timestamp in the current model. Local-row
  recency never fixes that provenance gap. UI conservatively stays unverified for that evidence;
  do not add a timestamp backfill or manufacture a successful reconciliation time.
- Bounded reads cannot recover evidence they were not given; this classification is a persisted
  snapshot report, never a replacement for successful venue reconciliation.

Cross-lane note is `/tmp/tt-implementation/agents/protection-interface-note.md`: reporting
unknown/stale must not disable risk-reducing supervision or change a deliberately paused choice.
