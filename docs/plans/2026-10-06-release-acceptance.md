# Runtime-safety release acceptance

Status: verification complete at `ab8527b`; release pending operator authorization. This is a
finite completion checklist, not a shipped claim. It supplements the [implementation plan](2026-10-06-runtime-safety-and-operator-truth.md).

## Scope and ownership

The scope is frozen to the verified review corrections and their integration. One implementation
owner controls the execution/store/wrapper transaction contract and its callers. Reporting has a
separate owner; the lead integrates, checks independent counterexamples, and controls the shared
schema, migration chain, final validation, PR and release. No additional feature expansion or
policy changes are part of this completion round.

## Required counterexamples and evidence

| Boundary | Required acceptance evidence | State |
| --- | --- | --- |
| Cancel and replacement protection | Execution learned during cancel cannot reuse the old quantity; empty/partial published fills stay unresolved, complete applied economics permits correct remaining cover; include stop-only and secondary products | Implemented; actual warming-caller cases passed |
| Discretionary reuse | Actual entry caller reads authoritative self accounting, rechecks eligibility, and cannot overwrite a concurrent financial/lifecycle update while persisting pending state | Implemented; actual FLAT-candidate late-fill PostgreSQL race passed |
| Quote-wide breaker pause | Independent peer fill, command and latch changes survive the conditional, product-neutral pause; deliberate pauses/stops and other quote/mode scopes are preserved | Implemented; memory and separate-engine PostgreSQL races passed |
| Scoped revision check | Failed parent CAS has zero runtime effects through both wrappers; restart/reload still supervises the filled product | Implemented; both wrapper orders and runtime-write rollback passed |
| Concurrent fill projection | Two independent PostgreSQL engines, distinct same-book fills and deterministic interleaving; both economics retained exactly once locally, sibling products preserved, duplicate replay harmless | Reproduced on PostgreSQL, corrected with parent row lock; lead rerun passed |
| Product mark attribution | Real scoped closed-bar callers retain the correct sibling marks despite very different prices; no false/concealed loss or erroneous durable latch | Implemented; actual scoped closed-bar positive/negative cases passed |
| Stopped time exit | Valid preview-only maintenance preserves the strategy's reached time exit without synthetic bars, guessed prices or cursor advancement | Implemented; actual managed-shutdown fallback passed |
| Reporting completeness | Missing occupied product with surviving sibling is unknown; prior incidents do not recover; portfolio equity/exposure/briefing totals stay null when dependent economics are unresolved | Integrated; 442 combined checks and 12 independent PostgreSQL restart/recovery cases passed |
| Browser consent | Deferred preview cannot replace reviewed target/latch revisions; failed requests retry identical consent and idempotency key | Nine focused browser tests passed; final combined checks pending |
| Delivery ownership | Disabled provider cannot revoke another dispatcher's unexpired claim; owner success/failure still acknowledges correctly across independent engines | Focused memory/PostgreSQL checks passed; final combined checks pending |

The six earlier core counterexamples were initially executed with fake brokers/in-memory stores.
The correction now adds isolated PostgreSQL evidence: distinct fills lost one cash delta before
locking, rejected parent writes leaked runtime changes, and the old discretionary caller admitted
a genuinely FLAT-listed candidate despite a late fill between parent/child reads. Migration-fixture
setup errors are not counted as reproductions. The lead's integrated rerun passed 242 core/risk/
worker/control/reporting checks, including separate-engine races. These focused results do not
replace final full suites and independent review; an atomic transaction alone never proves
serialization of two projections from the same base.

## Reporting-to-risk integration closure

After integrating the core correction, lead verification found one remaining consumer gap:
reporting recognized an occupied runtime without its position, but the no-price entry gate could
still allow and opening reconstruction could certify an empty-fill midnight balance. Eight
paper/live, old/new-book and observed/unobserved cases failed before correction. Risk admission,
daily PnL, opening replay and flat-day accounting now share the reporting predicate. Existing
independent budget/slot/exposure denials retain precedence. A pyramid-add positive fixture now
has actual applied entry evidence, matching paid cash and a real position; its missing-position
negative control still denies. The focused combined risk/reporting/execution selection passed
265 checks. This is a consumer integration correction, not a new feature or policy change.

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

Final candidate `ab8527b` (2026-10-07). GitHub CI against its PostgreSQL service, with both
isolated database settings: **3,535 backend tests passed, none failed or skipped**; Ruff, format
and type checks pass; frontend lint, Svelte check, 468 unit tests, all 237 Playwright E2E tests
and the production build pass. Alembic has the single head `0069`. GitNexus was reindexed with
PDG at the candidate; full-branch change analysis reports CRITICAL risk (287 files, 1,932 symbols,
586 flows, changed-symbol listing capped), which the full suites and the review below address.

An independent safety review of the integrated candidate found no blockers. It confirmed two
should-fix defects, both now corrected with failure-before/success-after tests:

- Portfolio supervision read an unresolved sleeve's null equity as zero PnL, so a hidden gain
  could trip a false drawdown stop, a hidden loss raised the high-water mark, and a day open
  recorded during the gap could mask real losses. Supervision now holds the run baselines and
  evaluates no new trip while a run book's ledger is unresolved; an existing latch still pauses.
- Unresolved inventory softened a live position with no working stop at all to a
  `STOP_COVERAGE_UNKNOWN` warning. It now stays critical `STOP_UNCOVERED`; reported protection
  evidence is unchanged.

Accepted notes, not changed in this round: the protective-replacement reconcile applies no
strategy cooldown when a stop fills during the replacement cancel, so live can re-enter sooner
than paper/backtest; summary views report every book `open_unverified`/`unknown` because they
omit full inventory evidence (documented, fail closed).

### Production rehearsal

A fresh read-only `pg_dump` of the operational database (revision `0064`, 47 deployments) was
restored into a disposable container. `alembic upgrade head` ran `0064 → risk0065 → 0066 → 0067 →
0068 → 0069` in 1.4 seconds and preserved every deployment status. The candidate's entry predicate
over the migrated copy found one unresolved book: live ADA-USDC `01a0fc0c-3981-72e4-a76d-b11ec2018370`,
paused in `pending_exit` after Coinbase rejected five protective brackets for insufficient funds.
Its bracket `01a113aa-3876-7731-b01d-d0c1d74d45c7` is recorded FILLED for the full 63.08999903 ADA
with no fill row, while the position still shows that quantity. Until that execution is
reconciled, the candidate denies every new live USDC entry (paper USDC is unaffected; exits and
protection are not gated). The candidate's paused-book reconciliation re-reads FILLED orders whose
applied fills are short and ingests REST fills; if Coinbase returns none, the book records
`Filled order has no REST fills.` and stays unresolved. The rehearsal container was removed.

### Remaining release steps (operator-authorized)

Fresh backup immediately before cut-over, prebuilt images, migration, service replacement, then
verification of the same intended running fleet, preserved pauses, protection, reconciliation of
the ADA book and the deployed SHA. No automatic resume, fill invention or policy change.
