# 0153: Held-only daily-loss marks, per-book candle windows, per-send claims, unique lease holders and settled runtime state

- Status: Accepted
- Date: 2026-10-29
- Relates to: [0115](0115-durable-safety-alerts-and-supervision.md), [0129](0129-paper-futures-books-and-shared-collateral-risk.md),
  [0131](0131-execution-cycle-timing.md)

## Context

A review of the execution worker and its supervision found five robustness gaps:

- Strategy and lockstep bars priced a last-close mark for every product of every retained
  same-mode book, stopped flat books included, and let a failed preview raise. A stopped book on
  a market that no longer serves previews then failed every other book's bar, and each bar paid
  one preview per retained product.
- Safety supervision cached closed candles by product and timeframe only, but each read is
  anchored at the book's deploy time. A newer book's later-anchored window was reused for an
  older book on the same product, which could hide that older book's earlier stop crossing.
- Alert dispatch stamped every claim in a batch with the batch start. A send can take up to 15
  seconds, so a late item's 60-second claim could already be expired when it was sent, letting a
  second dispatcher claim and send it again.
- The lease holder was `execution-worker:<pid>`. Every container runs the worker as PID 1, so
  two overlapping workers named themselves the same holder and the lease excluded neither. Order
  intents were not fenced either: a pass that outlived its 45-second lease could still submit.
- The operator `runtime` report built each book from the bounded summary, which never certifies
  accounting, so every book, flat ones included, read `open_unverified`.

## Decision

- **Marks only for held inventory.** Daily-loss marks are read for products a book holds,
  through the same helper fleet entry health uses (`inventory_marks`). A failed read leaves that
  product unmarked, so admission treats its loss as unknown and fails closed, instead of the
  unrelated bar failing.
- **One window per book anchor.** Stop-crossing supervision caches each window by product,
  timeframe and deploy anchor. The per-clock cache that the protection checks read is
  unchanged: it keeps the first window read, exactly the input those checks had before.
- **Claims at each send.** Dispatch advances each claim's timestamp by the wall time elapsed in
  the batch, so a claim lease always covers its own send.
- **Unique holders and fenced submissions.** A holder is
  `execution-worker:<host>:<pid>:<process token>`. A leased pass binds its book and holder
  (`trading/lease_fence.py`) around each intent write, and the store checks the book's current
  holder in the same transaction that inserts the intent: PostgreSQL reads it `FOR SHARE`, which
  waits out a concurrent lease UPDATE, and the in-memory store checks and inserts with no await
  between. An intent for a book another worker now holds is refused. An unleased book (stores
  without leases) and intents for sibling books are not fenced. A lease that merely expired,
  with no other holder, does not refuse: only another worker taking the book does. The
  refusal is a `LeaseTakenError`, never read as an idempotent duplicate of an earlier keyed
  order.
- **A clean stop releases its leases.** A worker that stops cleanly clears every lease it
  holds, so its successor maintains each book on its first visit instead of waiting out the
  TTL.
- **Runtime state from one coherent read.** The `runtime` report reads each book's full
  accounting snapshot, one repeatable-read observation per book, for position state,
  protection and the ledger flag. A settled flat book reads `flat`. When that read fails, or
  reads flat while an order is still in the reconcile watch set (a bracket sibling whose read
  failed, a working order no phase shows), the row falls back to the bounded summary and stays
  `open_unverified`.

## Consequences

- An unavailable market on a flat retained book no longer stalls other books. A held product
  whose mark fails still blocks new entries through the existing unknown-loss path.
- After a clean deploy the new worker takes each book on its first visit. After a crash, it
  waits for the old lease to expire, then for its next visit to that book: up to one TTL plus
  one cycle.
- The fence closes the gap between checking the lease and recording the intent. One window
  remains: a takeover that commits after the intent but before its order row is saved (a single
  database round trip) sees an intent with no order. Two workers never run together in the
  Compose deployment, so this is defense in depth.
- The fence covers order intents only. A stale pass can still cancel a resting order, or save
  a pending phase, before its replacement intent is refused; the successor sees the result on
  its next visit, and the refusal surfaces as a worker book failure. Fencing every write would
  need a lease check in each store transaction.
- Each fenced intent insert costs one locked read of the deployment row.
- The `runtime` report reads each book's full history once per report, one book at a time,
  as the worker does every cycle.
- A held product whose mark read fails now denies entries through `BREAKER_MARK_MISSING`
  rather than failing the bar. Fleet entry health reads the same marks, so a lasting failure
  still surfaces as `FLEET_ENTRIES_BLOCKED`.
- HTTP deployment summaries are unchanged and still report bounded-summary state.

## Alternatives considered

- **Renewing the lease during a pass.** A renewal bumps the deployment revision and would
  invalidate every in-flight deployment object the pass holds. Fencing the intent achieves the
  exclusion without touching revisions.
- **A deterministic idempotency key on protection intents.** Unique holders plus the intent
  fence already stop the second submission; a deterministic key would change client order ids
  that reconcile and replay packages depend on.
- **One batch read for the runtime report.** The batch reads each table separately, so a write
  landing mid-read could pair an old flat deployment with a newer entry order, and the worker
  bumps revisions every cycle, so a revision check would rarely certify an active book.
- **Keying the shared candle cache by anchor.** The protection checks read the per-clock cache
  directly, so a separate anchored cache keeps their lookup unchanged.
