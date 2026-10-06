# Execution evidence: lead-review corrections (2026-10-06)

Follow-up to initial local commit `c168f75`, ADR 0116, branch `feat/review-evidence`.
The initial completion claim was too strong: passing tests did **not** establish fee-population
honesty, partial-exit coverage, semantic twin compatibility, equal histories, or causal
slippage. Lead review exposed real defects in all of these. This follow-up fixes them; it
does not rewrite recorded fills, trading policy, published strategies, or the backtest engine.

## Five defects and fixes

1. **Unknown fees/liquidity and normalization population.** `fee_normalization.population`
   is explicitly `live_applied_fill_lifetime`, regardless of whether twins overlap. It counts
   every raw applied live fill exactly once and reports `fill_count`. The observed sum stays
   observed. Any unknown liquidity or excluded unapplied/orphan fill coverage makes the
   counterfactual total and `fee_delta` **null** with `complete=false`. There is no partial
   known-subset counterfactual presented against a full observed total and no implicit zero.

2. **Partial exits / raw facts.** Per-book `recorded_fills` carries all raw applied quantities,
   fees and identities exactly once. Open cycles now expose `exits`. Fee normalization uses
   only that raw list, not duplicated cycle views or clamped over-covering projections.
   Closed-cycle sums remain separate from open-cycle facts. Deployment slippage coverage
   now includes all applied fills, including still-open partial exits. The ledger residual
   can include partial-exit realization, clamping and rounding; it is not labelled as if
   rounding were the only possible source.

3. **Compatible rules / members.** Report `product_id` makes primary-market provenance
   explicit. Comparison checks link ids against both snapshots and reports, exact paper/live
   modes, market, clock, strategy binding and source fill economics. Clone equivalence uses
   the existing ADR 0105 `comparable_twins` proof (canonical content bound to exact snapshots).
   The twin GET route loads those pinned snapshots via `get_strategy_snapshot_store`; absent
   proof is `trading_rules_unverified`, different rules are `trading_rules_incompatible`.
   Both block comparison. Different identities alone are informational only **after** proof;
   legitimate paper fee assumptions need not match observed live fees.

4. **Equal histories / scope.** `population=recorded_fill_lifetime` is explicit. Lifetime
   net-PnL summaries are `summaries_context_only=true` unless evidence is complete and fill
   bounds match exactly, with equal proven product/decision-bar/purpose/side/quantity fill
   and order populations, including unmatched orders. Overlap remains contextual only.
   Unequal bounds, fill exposure or orders block comparison, even with identical counts and
   intersecting dates. Per-side applied-fill counts, unmatched-entry counts, available
   journaled-signal counts and time bounds are disclosed. This deliberately conservative
   implementation does not crop lifetime totals or invent signal/fill pairings.

5. **Causal slippage.** Match the exact persisted `intent.candle_starts_at`, not the fill's
   bar. Journal evidence includes its actual `bar_closes_at`, which must match the declared
   clock and be no later than intent creation, order creation and fill. Intent precedes
   order, order precedes fill, and product/side identities must agree. Attached children
   lacking an independent matching decision reference get null. New fill fields are
   `reference_price`, `reference_intent_id`, `reference_bar_starts_at`, and
   `reference_bar_closes_at`. These benchmark the original intent decision, **not** a
   contemporaneous quote at a later reprice. Missing/future references remain null. Journal
   paging follows the oldest needed intent bar even for delayed fills; reaching it is not
   erroneously flagged as a cap hit.

## Public surfaces / integration

Routes and CLI invocation are unchanged. Local evidence schemas remain named v1 because
these commits have not been released; global contract/version/schema integration belongs
to the lead. The operator skill documents the corrected meanings. The UI renders unavailable
counterfactuals explicitly (never money zero), identifies lifetime context-only summaries,
shows all applied fills including partial exits, and displays causal reference provenance.
ADR 0116 documents the corrected pre-release choice and acknowledges the initial defects.

No new changes to OPS_CONTRACT_ID, EXPECTED_SCHEMA_REVISION, global operator schema JSON,
global ADR index, migrations, execution models/persistence, or another lane's files. The
initial commit's ADR-index append is preserved. `lead-order-observation-interface-note.md`
and `lifecycle-ledger-interface-note.md` were read: no cross-lane source change is required.
Handoff: `/tmp/tt-implementation/agents/evidence-interface-note.md`.

## Regression coverage / verification

New tests: `tests/execution/test_execution_quality_regressions.py` and
`tests/api/test_execution_quality_review_regressions.py`. They exercise unknown-liquidity
costs, unapplied-fill coverage, open/closed partial exits, raw over-covering quantities,
unequal histories, same-boundary unequal quantities and orders, proof-free/incompatible/
equivalent/invalid clones, member/report mismatches, absent/future/late/attached references,
and delayed-fill paging. HTTP checks also assert JSON null costs and pinned-rule reads.
Original fixtures are corrected to include explicit completed-bar intents rather than
continuing their future-close benchmark. UI helper tests preserve null vs recorded zero.
`web/src/lib/executionQualityRendering.spec.ts` additionally renders the real Svelte report
in-process (no servers) and asserts unavailable costs, context-only summaries, raw partial
exits counted once, and reference provenance/missing references.

Verification:

- Focused backend evidence suites: **58 passed**, including **34 new regressions**.
- `uv run pytest -q`: **2828 passed, 89 skipped**, 2 existing warnings. No PostgreSQL test
  URL was configured; integration tests remain hermetically skipped.
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check`: clean.
- Frontend `npm run check`: 0 errors/0 warnings; `npm run lint`: clean;
  `npm run test:unit -- --run`: **438 passed / 48 files**, including SSR report tests;
  `npm run build`: succeeds. No browser/production-server E2E test was run.
- GitNexus upstream impact ran locally before edits. Model/internal helpers resolve LOW;
  the two public builders and decorated GET handler are UNKNOWN (dynamic `asyncio.to_thread`
  and router registration are not fully resolved). Source inspection confirmed the evidence
  route callers; executed HTTP tests cover them. No core execution, ledger or twin-rule
  implementation is changed.
- A fresh local incremental graph rebuild used `--name tt-review-evidence --workers 1
  --embedding-threads 1 --index-only --pdg --embeddings`, covering 84,242 nodes / 190,417
  edges. Embeddings were skipped at the 50,000-node safety cap. Latest local
  `detect-changes --scope all`: **14 files / 149 symbols, LOW**, zero resolved affected
  processes. This is **not** an all-clear: the analyzer explicitly reports capped process
  enumeration (2,523 dropped entry-point candidates, 650 skipped callees and bounded traces),
  in addition to the dynamic-call gaps above. Direct source/API regression verification is
  authoritative; the lead still runs integrated graph/contract checks. The root graph was
  not changed.
- `git diff --check`: clean. Follow-up touches only evidence-owned files, tests, ADR 0116,
  the operator skill and slice notes; no global release contracts or ADR index changes.

## Remaining limits (explicit, not hidden completeness claims)

- Identical lifetime fill bounds and population structure are a conservative requirement.
  Different execution timing, sizing or fill splits can legitimately cause cannot-compare;
  this version does not implement an aligned shared-window reconstruction.
- Signal populations are not individually paired; missing signal counts remain null.
  The comparison scope is recorded fills/orders, not a claim of equal strategy-wide signals.
- Original-intent close is not the submission-time market quote for long-resting/repriced
  orders. Missing independent attached-child references are not backfilled from parent fills.
- Journal reads remain capped at 25 pages of 200 rows per product; missing older intent
  closes produce incomplete evidence. There is no historical data mutation or fallback mark.
- All tests are hermetic. No production operations, services, credentials or PostgreSQL.
