# Core execution/persistence boundary completion — 2026-10-06

## Scope and ownership

Bounded completion on `feat/review-core-transaction-boundaries`, base `6d82812`:
six reproduced execution/risk findings plus the independently tested distinct-fill
concurrency concern. No new features, policy changes, financial-history rewrites,
new dependencies, schema/migration changes, production operations, or venue calls.
Reporting-owned protection/ledger/readiness/alerts/portfolio/browser modules are untouched.
The lead owns global operator/skill/ADR wording, final reindex, integration, and independent
acceptance. This plan is implementation evidence, **not release certification**.

The early interface proposal was published at
`/tmp/tt-implementation/agents/core-boundary-interface.md` before substantial API changes.
All prerequisite reports, their reproduction source/logs, and ADRs 0057/0058/0110/0111/0120
were read. Prior PostgreSQL claims were treated as source-supported, not executed proof.

## Internal contract

### Conditional parent and focused runtime

`ExecutionStore.save_deployment` accepts an optional `instrument_runtime` alongside
`expected_revision`. Supplying runtime requires a revision. The parent revision decision and
focused runtime write are one operation: a rejected CAS has **zero** parent/runtime effects.

- Memory checks before writes and does not yield between parent/runtime assignments.
- PostgreSQL performs the conditional parent UPDATE (which obtains the existing row lock),
  optional runtime upsert, and commit in the same transaction. Runtime errors roll back the
  parent too. Revision increments from the stored row, never the stale input revision.
- `InstrumentScopedStore` reads unfiltered authoritative parent/runtime state, constructs
  aggregate phase retaining siblings and parent decision cursor, and supplies the caller's
  loaded revision by default. It no longer commits a standalone runtime upsert.
- An explicit runtime traversing another scope is already paired with a shared parent; nested
  wrappers forward it unchanged instead of rebinding its product.
- `RevisionFencedStore` forwards runtime and advances its cached fence only after success.
  Its own cached fence must equal the passed deployment revision: applying a fill through
  this wrapper does not authorize an old caller snapshot to overwrite the newer cash/runtime.
  Both wrapper orders are tested, including this self-refreshed-fence case.

Existing whole-row calls without a revision retain their prior semantics; this is not a claim
that every unfenced writer in the application was redesigned. Revision increment is monotone
for those calls as well. Scoped saves and the concrete repaired paths are conditional.

### Breaker-owned metadata

`save_breaker_pause(id, expected_revision, detail, daily_loss_latched=False)` is product-neutral.
It owns only RUNNING → PAUSED plus that running row's detail, optional **set-only** daily latch,
updated time and revision. It never rewrites cash, aggregate phase, product runtimes, lifecycle
command, leases, drawdown latch, capital, or opening evidence. PAUSED/STOPPED intent and detail
are preserved even when the retained row is the daily-latch anchor.

Quote-wide pause freshly revalidates each peer's full mode/quote evidence and uses its own CAS,
not the triggering product or lease. A revision race retries at most three times with fresh
reads; exhaustion propagates a conflict rather than committing stale state. Both wrappers
forward this operation neutrally and update a cached fence only for their own deployment.

### Reused candidate and fill projection

A reused discretionary book uses `accounting_snapshot` for self, rechecks RUNNING/FLAT,
identity/product/mode, absence of positions/working orders, lifecycle intent and paper fees.
Its pending write uses that same loaded revision. A lost CAS stops before a new intent or
venue submission; retry requires fresh admission. A genuinely new unpersisted candidate
continues to use constructor evidence before creation.

PostgreSQL fill application locks the existing deployment row **before** reading children or
projecting economics. Same-book distinct fills serialize across independent engines.
Exact duplicate venue-fill observations apply local economics once. Network I/O is outside
these transactions. This is neither venue exactly-once execution nor account-wide reservation.

## Finite acceptance matrix

| Item | Change | Executed evidence |
| --- | --- | --- |
| 1. Cancel learns additional execution | After confirmed replacement cancel, reconcile REST fills and recheck complete economics/current projected position before constructing replacement. No watermark-derived quantity. Known preexisting unresolved evidence still prevents useful-cover cancellation. | Actual worker warming fallback; BTC and ETH; stop-only and bracket; empty/partial/full publication during cancel; reload and next maintenance. Empty/partial submits none; fully applied `0.004` allows exact `0.006`, not old `0.010`. |
| 2. Reused discretionary self/pending write | Authoritative self read, fresh eligibility, pending CAS. | Actual `place_discretionary_order` with FLAT listing and independently committed late canceled-entry fill between parent/child SELECTs. Repeatable read retains old complete view; pending CAS rejects newer fill revision before intent/submission. Separate lifecycle race and recorded-loss denial. |
| 3. Quote-wide peer pause | Per-peer product-neutral metadata CAS/revalidation/retry. | Actual scoped breaker chain races peer ETH fill plus lifecycle/drawdown latch before pause, in memory and independent-engine PG; preserves cash, runtime, command/revision and deliberate concurrent STOPPED status. Adjacent quote/mode/deliberate-pause/latch tests pass. |
| 4. Rejected scoped save | Atomic parent/runtime operation and wrapper fence discipline. | Memory and PG with both wrapper orders; concurrent applied ETH entry at write boundary; failed performance CAS; reload retains OPEN runtime, cleared pending levels and correct cash; subsequent due-bar maintenance submits correct ETH protection. Also self-refreshed-fence rejection and actual PG runtime-error rollback. |
| 5. Secondary candle attribution | Runtime breaker takes explicit actual `product_id`; production caller supplies `product.product_id`. | Actual ETH-scoped `process_closed_bar` with genuine BTC midnight opening proof and widely divergent sibling marks; neither false latch with `{BTC:100, ETH:1}` nor concealed loss with `{BTC:1, ETH:100}`. |
| 6. Stopped preview-only time exit | Forward valid strategy to missing-full-window fallback. | Actual stopped managed-shutdown supervisor, genuine warming exception and verified ETH preview, persisted reached time exit: marketable TIME_EXIT, not re-rested bracket; cursor unchanged, no invented candles/bars. |
| 7. Concurrent distinct fills | Existing parent FOR UPDATE before projection. | Two independent engines and deterministic post-parent-read transaction barrier; same product and secondary product; observed real PG blocking; exact cash/positions, two applied markers and two revisions; duplicate replay unchanged; concurrent exact duplicate applies once; fresh reload followed by correct per-product protection. |

## Failed-before and passed-after evidence

Scratch logs under `/tmp/tt-implementation/agents/core-boundary-*`:

- `before-repros.log`: all six prior fake-only counterexamples re-executed successfully on base.
- `before-memory.log`, `before-cas-memory.log`: new actual-caller counterexamples fail as expected;
  the initial harness also exposed a broker-attribute typo and the intentionally new private
  product argument, corrected without weakening financial assertions.
- `before-pg-verified.log`: **4 failed / 1 passed** with usable offline PG fixture. Both distinct
  fill races produce cash `899.9` instead of `799.8`; both wrapper orders restore PENDING_ENTRY
  runtime after a rejected parent CAS. Earlier `before-pg*` logs retain fixture setup errors
  (unrelated metadata NullType, missing inhibition seed, cloned unique-constraint name); those
  are explicitly **not** financial defect proof.
- `before-pg-actual-reuse-observed.log`: a negative control restores only base discretionary
  module code in an isolated process, leaving current files intact. The real PG FLAT-listing
  race admits and returns cash `1000` despite independently applied cash `899.9`, one BTC
  position and three intents. The expected conflict regression fails. The corrected caller
  passes with a consistent candidate read and no new submission.
- `before-refreshed-fence.log`: opposite-order wrapper could bless an old snapshot after its
  own fill refreshed the cached revision. Narrow wrapper revision check closes it.
- `focused-execution-final.log`, `focused-worker-risk-cli-final.log`, `focused-pg-final.log`:
  **184 execution/risk/fleet**, **188 worker/risk/runtime-CLI**, and **20 PostgreSQL** tests
  pass (the hermetic groups overlap; these are not unique-test counts). The PG run has one
  existing Alembic `path_separator` deprecation warning. Repository-wide Ruff, format and
  `ty` checks pass. No full backend/frontend suite run here.

PG uses only the approved offline loopback test instance. New races create/drop unique scratch
schemas, cloning current execution columns/checks/indexes and restoring execution foreign keys
and the named fill-uniqueness constraint. Synthetic intents/orders satisfy these relationships.
The broader existing PG tests run against a uniquely named throwaway database upgraded to head
using the existing sanitized `_alembic` helper (dotenv disabled in the child), then dropped.
No shared base downgrade, `.env`/credential access, Coinbase, production DB or deployment actions.

## Caller/graph audit and remaining gates

The lead rebuild exit was 0 before queries/edits. Explicit registered root graph query/context/
impact was used. Protocol impact is **CRITICAL**: 126 upstream symbols, 45 direct callers, five
process groups (resume, latch reset, discretionary HTTP order, closed-bar processing, fleet
recording), four modules. Concrete store/dynamic fill edges with no callers are **UNKNOWN**, not
unused. Source inspection plus real wrapper/caller tests are the authority.

All production `ExecutionStore` implementations and the scoped/fenced proxies support the new
contract. Actual optional runtime writer is the scoped wrapper. Existing raw standalone runtime
forwarders remain available, but scoped saves no longer use them. `ingest_fill` selects the atomic
transaction on memory, PostgreSQL and both wrappers; its historical nontransactional fallback
is not selected by these production stores. No new fallback or separate runtime post-commit
write was added. Private breaker caller and existing test hooks were updated explicitly; no
reporting-owner API changes.

Root graph remains at base `6d82812`; the root has also advanced with independent lead/reporting
work, and this worktree's new symbols/edges are not indexed. Staged worktree-aware `detect_changes(scope=all)` reports all 14 changed files, 55 indexed
symbols and 13 affected processes, **high** risk, without partial/truncated flags; newly added
symbols still require reindexing and line-shift mappings can include unchanged neighbors.
Route map returns 186 existing routes. Shape check finds no paired route/consumer shapes, so
its zero is **not** compatibility certification. Structural cycle enumeration is complete but
fails on three **preexisting base-index** cycles (memory/fleet-store type edge,
worker service/venue, and deployment-detail/strategy-workspace). No imports forming these
cycles changed; do not suppress or call this a passing structural gate. These checks do not
replace the lead's required serialized reindex with embeddings/PDG and integrated
change/route/shape checks. No heavy worktree graph
build is authorized here. Global docs/skills/ADR wording and full integrated suites remain lead
integration gates. Dynamic edges, bounded race coverage, external venue behavior, arbitrary
providers and unfenced whole-row writers outside these repaired paths are not release-certified.
