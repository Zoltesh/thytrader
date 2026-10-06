# Runtime-safety release acceptance

Status: implementation and verification in progress. This is a finite completion checklist,
not a shipped claim. It supplements the [implementation plan](2026-10-06-runtime-safety-and-operator-truth.md).

## Scope and ownership

The scope is frozen to the verified review corrections and their integration. One implementation
owner controls the execution/store/wrapper transaction contract and its callers. Reporting has a
separate owner; the lead integrates, checks independent counterexamples, and controls the shared
schema, migration chain, final validation, PR and release. No additional feature expansion or
policy changes are part of this completion round.

## Required counterexamples and evidence

| Boundary | Required acceptance evidence | State |
| --- | --- | --- |
| Cancel and replacement protection | Execution learned during cancel cannot reuse the old quantity; empty/partial published fills stay unresolved, complete applied economics permits correct remaining cover; include stop-only and secondary products | Pending correction/verification |
| Discretionary reuse | Actual entry caller reads authoritative self accounting, rechecks eligibility, and cannot overwrite a concurrent financial/lifecycle update while persisting pending state | Pending correction/verification |
| Quote-wide breaker pause | Independent peer fill, command and latch changes survive the conditional, product-neutral pause; deliberate pauses/stops and other quote/mode scopes are preserved | Pending correction/verification |
| Scoped revision check | Failed parent CAS has zero runtime effects through both wrappers; restart/reload still supervises the filled product | Pending correction/verification |
| Concurrent fill projection | Two independent PostgreSQL engines, distinct same-book fills and deterministic interleaving; both economics retained exactly once locally, sibling products preserved, duplicate replay harmless | Source concern; database proof required |
| Product mark attribution | Real scoped closed-bar callers retain the correct sibling marks despite very different prices; no false/concealed loss or erroneous durable latch | Pending correction/verification |
| Stopped time exit | Valid preview-only maintenance preserves the strategy's reached time exit without synthetic bars, guessed prices or cursor advancement | Pending correction/verification |
| Reporting completeness | Missing occupied product with surviving sibling is unknown; prior incidents do not recover; portfolio equity/exposure/briefing totals stay null when dependent economics are unresolved | Integrated; 442 combined checks and 12 independent PostgreSQL restart/recovery cases passed |
| Browser consent | Deferred preview cannot replace reviewed target/latch revisions; failed requests retry identical consent and idempotency key | Nine focused browser tests passed; final combined checks pending |
| Delivery ownership | Disabled provider cannot revoke another dispatcher's unexpired claim; owner success/failure still acknowledges correctly across independent engines | Focused memory/PostgreSQL checks passed; final combined checks pending |

The six earlier core counterexamples were executed with fake brokers/in-memory stores;
PostgreSQL consequences require real isolated database evidence. In particular, the exact
reused-book interleaving must respect earlier FLAT eligibility, not depend on an impossible
fake-store time sequence. A migration-fixture setup error is not a concurrency reproduction.
An atomic transaction alone does not prove serialization of two projections from the same base.

## Integration and release gates

1. Focused failure-before/success-after checks for the matrix, including real separate-connection
   PostgreSQL races and reload followed by the actual protective/entry caller.
2. Reconcile reporting and core changes on one candidate; check nullable API/TypeScript consumers
   and regenerate source-derived operator schemas. Preserve ops v68 and the single current head
   `0069` unless a separately reviewed contract change is genuinely necessary.
3. Run the complete Python suite with both isolated database settings, migration/metadata checks,
   Ruff/format/typing, frontend lint/type/unit/render/E2E and production build at the final candidate.
   A previous commit's pass count and a focused pass are not final acceptance.
4. Inspect current full-branch graph/diff/route/shape evidence and independent safety review.
   Disclose capped discovery, missing dynamic/cross-language edges and unsupported shape checks.
5. Only after acceptance: fresh production preservation baseline and backup, prebuilt images,
   reviewed PR/main, compatible migration/service sequencing and exact deployed-SHA verification.
   Preserve deployment identities, strategy/policy snapshots, financial history, credentials,
   deliberate pauses and the existing follow-up timer. No historical fee rewrite, inferred exit
   quantity, policy weakening, blanket account-audit claim or automatic resume is authorized.

## Current evidence checkpoint

Root `6d82812` corrected immutable browser consent, active alert-delivery ownership and the five
failures from the last full Python run at `6576811` (3,378 passes / five failures). The affected
83-test run and four retained-evidence checks passed; the full run must still be repeated.
Initial projection observability was integrated as `e2c9fdd`; 249 combined reporting/risk/alert/
API/schema tests passed afterward. Product-runtime/portfolio reporting followed as `f48c81a`;
442 combined reporting/risk/alerts/portfolio/API/schema checks passed with both private database
settings. The lead's separate PostgreSQL regression passed all 12 paper/live, running/paused/
stopped, OPEN/PENDING_EXIT combinations after engine restart: missing ETH remains unknown beside
surviving BTC and prior incidents stay open until explicit FLAT runtime evidence appears. It uses
real migrations in an exclusively owned test schema, does not infer exit quantity, and verifies
that reporting preserves status and BTC inventory. Its first paper fixture was rejected for
missing required fee configuration; the corrected fixture supplies explicit test fees without
weakening that database constraint. Final combined suites and the core transaction-boundary
corrections are not certified by these focused counts.

The root graph was refreshed at `6d82812`: 93,752 nodes, 210,733 edges, 763 discovered flows.
It reports 2,800 dropped entry-point candidates, 33 budget-cut walks, four depth caps and 621
skipped callees. Existing embeddings are not a complete new embedding pass. Current ledger
upstream impact is **CRITICAL** (35 mapped symbols across risk, execution, API and operator
surfaces); graph caps and empty caller sets cannot waive the source/caller tests above.
