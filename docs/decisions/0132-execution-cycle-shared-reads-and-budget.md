# 0132: Execution cycle shared reads and slow-cycle budget

- Status: Accepted
- Date: 2026-10-10
- Relates to: [0095](0095-sparse-markets-no-trade-bars-listing-floors.md),
  [0113](0113-deploy-anchored-window-cache.md),
  [0131](0131-execution-cycle-timing.md) (amends §5)

## Context

ADR 0131's timing, deployed on 2026-10-10, measured the live execution worker (47 running, 9
paused and 58 stopped books; 30 s interval). Two consecutive steady-state cycles took 143 s and
141 s:

- `risk_snapshots` 67.7 s: the cycle reloads every book's full snapshot before and after each
  book it processes, so the entry gate's fleet evidence stays current. That is 115 reloads of
  114 books with six statements each: 78,660 SQL statements (25 s of database time, the rest
  per-statement overhead) on a table set holding about 300 rows.
- `books` 70 s, of which 60 s was 317–430 sequential Coinbase REST requests: product metadata
  (`/products/{id}`, 113–171 per cycle) and candles (113–171) dominated, read twice per window
  (once by the recent preview, once by the range tail) and repeated for every book on the same
  product; the fee tier (`/transaction_summary`) was read once per live book (31).
- Supervision phases took about 3 s.

## Decision

1. **Batched fleet reload, same evidence.** `DeploymentSnapshotBatchReader.get_deployments`
   (implemented by the PostgreSQL store) loads every requested snapshot with one statement per
   table and assembles each exactly like `get_deployment`. The cycle still reloads the whole
   fleet before and after each book; only the number of round trips changes. Snapshot row
   order gains deterministic tie-breakers (`id` after equal timestamps, positions and overlays
   by product) in both loaders, so both return identical tuples.
2. **Shared identical reads within a cycle.** The worker binds a `CycleReads` scope per cycle.
   Inside it, product metadata (per client and product), recent previews (per provider,
   product, interval and closed-bar boundary, so a bar closing mid-cycle is read afresh) and
   the fee tier are read once and reused by later books in the same cycle. Failed reads are
   never stored: the next caller reads again, so per-book error behavior is unchanged. Outside
   a cycle (API, other workers) nothing is shared.
3. **Not shared:** candle ranges. The deploy-window cache re-fetches a sparse block to confirm a
   missing bar (ADR 0095) and re-reads the mutable tail on every call (ADR 0113); both must stay
   real venue reads. Balances (`/accounts`) are not shared either: an order placed by one book
   changes what the next book may spend. Order and fill reconciliation is untouched.
4. **Slow-cycle budget.** `CYCLE_SLOW` now fires when a cycle (or the running one) exceeds its
   interval plus 30 s (`budget_seconds`), the slack health already grants every worker loop:
   past it, the cycle-start heartbeat of ADR 0131 would have gone stale. Below it the
   component is `CYCLE_WITHIN_BUDGET` and its detail still shows duration against interval.
   ADR 0131 graded against the bare interval, which would report a fleet-sized cycle that is
   safely within the liveness budget as permanently degraded.
5. `book_groups` (per status and mode totals) and `shared_reads` join the cycle report. Ops
   contract v92 (`execution_cycle_budget`).

## Consequences

- Decisions, orders, protection, reconciliation and risk evidence are unchanged: a fixture
  fleet run through the original and the new cycle produces identical journals, intents,
  orders and book state; the batched read equals per-book reads on PostgreSQL.
- Books on the same product and clock in one cycle see the same product metadata, preview
  and fee tier, observed by the first of them seconds earlier rather than by each.
- The cycle still reads balances, candle tails and orders per book; a much larger fleet would
  need concurrency, which this ADR does not introduce (leases and the per-book fleet reload
  make book processing sequential by design).

## Alternatives considered

- **Reload only the processed book.** Rejected: other books can change under the API mid-cycle
  (operator stops, manual intents) and the store has no cheap change signal covering intents,
  orders, fills and positions.
- **Share candle ranges per cycle.** Rejected: it would turn the window cache's confirming
  re-fetch into a repeat of the first observation.
- **Process books concurrently.** Rejected for now: it changes the order in which books see
  each other's entries.
