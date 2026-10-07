# Narrow protection provenance integration (ADR 0112 / 0119)

Worktree: `/home/hermes/projects/tt-review-provenance`, branch
`feat/review-protection-provenance`, based on integrated lead `7a18620`.
This follow-up consumes the lead-owned `Order.venue_observed_at` addition. Local commit only;
no push, merge, production access, credentials, `.env` reads, database connections (including
5439), operational actions, or deployment. Root and integrated release verification remain
lead-owned. Python uses a newly created local `.venv` from `uv sync --frozen`, editable-installed
only to this worktree. Frontend dependencies were installed locally with
`npm ci --ignore-scripts --no-audit --no-fund`.

## Behavior and scope

Only `execution/protection.py`, its presentation helper, corresponding regression tests, and
protection documentation/skill text changed. No worker, broker implementation, persistence,
migration, global ops contract/schema, lifecycle, or gate edits.

- Live coverage uses actual identified order-state receipt provenance, never `updated_at`.
  Missing, naive, future, stale, or UNKNOWN evidence contributes no confirmed quantity.
- Each partial stop needs its own fresh receipt within 120 seconds. `observed_at` is the latest
  relevant receipt; `verified_at` is the oldest contributing fresh OPEN receipt. On partial
  coverage the latter verifies only that fraction, not the entire book. Mixed stale/unknown
  matching candidates keep conservative freshness even when another row was just written.
- Duplicate folding uses venue-receipt order, ignoring local rewrite times. UNKNOWN/missing
  provenance and geometry/original-quantity conflicts are unresolved. Tied status conflicts,
  terminal-to-active histories, and regressing fills also fail closed in any tuple order.
  Distinct quantities add; duplicate quantities never sum.
- Profitable trailing stops can still cross entry. Executable stop kinds, exact stop/target
  matching, closing side, and remaining quantity requirements from `f808b65` remain.
- Fresh state plus matching submitted geometry can classify `covered`, but the UI says amber
  **Order state fresh**, explicitly **venue geometry not independently verified**. Receipt
  metadata cannot support an independent geometry audit, whole-account reconciliation, current
  mark/liquidity verification, or guaranteed stop execution. Frozen responses lose the fresh
  label at render time. Paper remains **Worker stop**, synthetic, with null receipt times;
  legacy/local-only evidence remains unverified.

## Lead-owned interface follow-up

`ProtectionEvidenceResponse` now emits `observation_source: venue_order_state` for actual
receipts, retaining `persisted_order` for local/legacy evidence, plus `synthetic_worker` / `none`.
`freshness` is `recent_venue` / `stale` / `unknown`; `venue_evidence_stale` replaces the old local
age reason. Backend no longer emits `recent_local`; the frontend still accepts that legacy value
but never promotes it to fresh venue state. `geometry_basis` values are unchanged and explicitly
refer to persisted submitted geometry.

The **global operator schema and ops contract are intentionally untouched**. Lead must regenerate
and integrate these changed enums/reason semantics before release. Operator skill, user guide,
agent integration, and ADR 0112 document the reporting semantics in this change. No operational
policy is inferred from a reporting classification.

## Exact focused verification

All Python test invocations explicitly unset both database-test URL variables. Tests use hermetic
fixtures and in-memory brokers/stores. No full suite, graph rebuild, E2E service startup, production
build, or database-backed verification was run for this resource-constrained follow-up.

```bash
env -u THYTRADER_TEST_DATABASE_URL -u THYTRADER_INTEGRATION_DATABASE_URL uv run pytest \
  tests/execution/test_protection.py tests/execution/test_protection_evidence.py \
  tests/execution/test_protection_review.py tests/execution/test_protection_provenance.py \
  tests/execution/test_position_state.py tests/execution/test_venue_observation.py \
  tests/execution/test_take_profit_none.py tests/operator_diagnostics/test_deployment_books.py \
  tests/api/test_book_marks_api.py tests/api/test_deployments.py -q
```

**152 passed**. The first expanded run found two old stop-only tests incorrectly claiming coverage
immediately after submission, with no status receipt. Their regression now asserts `unknown`
before a real in-memory broker reconciliation read, then `covered` after that read; no invented
fixture timestamp or execution change. The isolated stop-only rerun passed **9 tests**.

- `uv run ruff check` and `uv run ruff format --check` on `src/thytrader/execution/protection.py`
  and the six changed execution test modules: passed.
- `uv run ty check`: passed.
- From `web`: `npx prettier --check src/lib/protection-evidence.ts
  src/lib/protection-evidence.spec.ts` and `npx eslint` on those same files: passed.
- `npm run test:unit -- --run --project server src/lib/protection-evidence.spec.ts`:
  **1 file, 5 tests passed**.
- `npm run check`: **0 errors, 0 warnings**.
- `git diff --check`: passed.

Regressions exercise actual LIVE reconciliation success, UNKNOWN and read-error invalidation,
fresh local writes over stale evidence, exact boundary age, mixed-freshness quantities, oldest
contributing receipt, terminal/UNKNOWN duplicate precedence, tied/conflicting histories,
partial-fill regression permutations, paper/legacy distinction, strict API serialization and
portfolio projections, frontend labels, and render-time aging.

## Read-only graph review and remaining verification

Per assignment, reused completed `/home/hermes/projects/tt-review-protection/.gitnexus` at
`f808b65` without writing/rebuilding it. Read its resource context, status, query, symbol context,
and upstream impact. The old index is complete for its checkout, but resource context against
the integrated HEAD reports **10 commits behind**. Integrated re-indexing remains pending.
`book_protection_evidence` upstream risk is **CRITICAL**: deployment serializers, operator
summaries, and portfolio/manager views consume it; internal classification/folding changes are
HIGH. Those reporting paths received focused regression coverage; lifecycle remains untouched.

Read-only `detect_changes(scope=all)` used this worktree's Git directory/worktree override with
the completed protection graph. The full structured result was inspected, not just the CLI's
short display: no `partial` or `truncated` flag; **HIGH** risk and ten affected reporting flows.
This is old-index impact review, **not** integrated symbol/line-hunk verification: new symbols and
integrated offsets need lead's serialized re-index. `route_map` finds 173 old-index routes;
`shape_check` has no routes with both shapes and consumers, so provides **no shape assurance**.
Source inspection, strict response serialization, API tests and TypeScript checks are the
available authority. Structural cycle enumeration is complete and finds the two previously
known cycles (execution-worker service/venue and deployment-detail/strategy-workspace), neither
touched here. Its CLI exits 1 for existing cycles; this is not a clean-cycle claim.

Lead still owns integrated embeddings/PDG indexing, complete verification, global schema/contract
integration, and any eventual release. Receipt freshness is bounded status evidence only;
independent venue stop geometry and full-account reconciliation remain outside this payload.
