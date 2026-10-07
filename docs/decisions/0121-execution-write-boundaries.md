# ADR 0121: Conditional execution writes and serialized fill projection

- Status: Accepted
- Date: 2026-10-06
- Related: [ADR 0057](0057-atomic-fill-ledger-and-product-isolation.md),
  [ADR 0110](0110-stopped-lifecycle-reconciliation.md),
  [ADR 0120](0120-verified-risk-opening-evidence.md)

## Context

An authoritative accounting read is necessary but cannot protect a later stale write.
Independent review found a product runtime committed before a rejected parent revision check,
unfenced discretionary pending state and quote-peer breaker writes, and replacement protection
sized from a position captured before cancellation disclosed execution. Separate PostgreSQL
connections also reproduced distinct fills projecting from the same parent cash: an atomic
transaction did not itself serialize read/project/write operations.

## Decision

1. `save_deployment` accepts an optional product runtime only with an explicit expected parent
   revision. Parent update and runtime upsert share one transaction. A failed revision check or
   failed runtime write leaves neither effect. In memory, the equivalent mutations are non-yielding
   after validation. Revisions increment from stored state, not a caller's stale counter.
2. Product-scoped wrappers build the shared parent and submit its runtime under the caller's
   revision, rather than committing a runtime separately. Nested scopes forward an already
   constructed runtime without rebinding its product. A revision-fenced wrapper may not bless an
   old caller snapshot merely because its own fence advanced after a fill.
3. Quote-wide breaker pauses use a product-neutral, revision-conditional metadata operation.
   It pauses a currently running book, preserves an already paused/stopped status and its detail,
   and may set but never clear the daily-loss latch. It does not replace cash, positions, runtime,
   lifecycle intent, leases or other latches. Each peer is reread and scope-checked; revision races
   get bounded retries, then a conflict rather than an unfenced fallback.
4. Fill application locks the existing deployment row before reading children or projecting
   economics. Distinct same-book fills serialize, including different products. The applied-fill
   marker and financial projection still commit together; duplicate replay remains harmless.
5. Reused discretionary entry reads authoritative self accounting, rechecks eligibility and
   conditionally persists pending state. A conflict occurs before intent creation/submission;
   retry requires fresh admission, not a stale request written against a newer revision.
6. Protection replacement reconciles execution disclosed during cancellation and revalidates
   economics/current projected inventory before sizing. Incomplete fill publication permits no
   guessed replacement quantity. Known uncertainty before cancellation retains useful cover.
7. Closed-bar breaker evaluation receives the actual candle product explicitly. Preview-only
   stopped maintenance retains strategy time-exit semantics without synthetic bars or cursor
   advancement.

## Alternatives

- Separate runtime write then parent CAS: rejected because a failed operation leaves observable
  runtime changes that can suppress protection after restart.
- Re-read before an unconditional whole-row write: rejected because another writer can still
  change state between the read and write.
- Retry every fill under optimistic CAS or make the whole store serializable: possible, but a
  per-parent row lock directly serializes the existing projection boundary without migrations,
  replaying domain work or introducing broad retry semantics.
- Reuse the scoped whole-row save for a peer pause: rejected because breaker authority is over
  narrow metadata, not that peer's financial state or a product runtime.

## Consequences and limits

No migration, public payload, CLI flag, risk limit or fee-history rewrite is required. Same-book
fill transactions may wait on one another; unrelated books retain concurrency. This is local
financial idempotence, not a promise of exactly-once venue execution. Existing unguarded whole-row
writers outside these repaired paths are not universally redesigned or certified by this ADR.

The [boundary implementation/evidence note](../plans/2026-10-06-core-transaction-boundaries.md)
and [release acceptance matrix](../plans/2026-10-06-release-acceptance.md) separate focused
counterexamples from final integrated and independent acceptance. An implementation commit is
not deployment evidence.

## Amendment (2026-10-07): single-product runtime ownership

Product-scoped wrappers created overlay rows for single-product books, while the closed-bar
loop kept advancing the deployment row. The lagging overlay made between-bar maintenance replay
every evaluated bar without entries and relabel its journal entry `CATCH_UP`; with an open
position it re-processed the bar. For a book whose overlay rows are all its own product, the
deployment row now owns runtime state: overlay reads come from it, a scoped save of that product
also writes the deployment row's runtime fields, and a plain save refreshes the existing mirror
row in the same transaction. Multi-product overlays keep their own authority and no row is
created by a plain save.
