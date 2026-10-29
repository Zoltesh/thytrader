# 0150: The working-entry lifecycle fails safe without stranding the book

- Status: Accepted
- Date: 2026-10-29
- Relates to: [0058](0058-protection-lifecycle-accounting.md), [0124](0124-inventory-adoption.md),
  [0125](0125-correlation-aware-risk-limits.md)

## Context

A review of the closed-bar order lifecycle found several paths where a working entry or its
fills put a book into a state nothing recovers from, or into a wrong one:

- A filled entry's venue-attached take-profit/stop child carries its parent's ENTRY intent, so
  entry-wait management treated the live bracket as an unfilled entry and cancelled or
  repriced it once `max_entry_wait_bars` passed.
- With `on_unfilled_entry: reprice`, a book that could not reprice (paused, breaker latched,
  shutting down) returned before cancelling, so the expired entry kept resting and could fill
  after entries were blocked.
- A reprice that could not go ahead after the cancel (no maker price, no ATR, a sizing skip, a
  replacement the venue rejected) left the book pending entry with nothing working, which the
  next pass paused as "entry lifecycle did not advance". A successful reprice then saved on a
  stale revision and failed after the replacement existed.
- A transient empty book or failed book read at entry time paused the book with "Maker entry
  price is unavailable.", a reason no path clears.
- A live discretionary book with no live broker bound fell back to the paper broker during
  maintenance, so a crossing candle booked a simulated fill on the live book.
- Stores load orders and fills newest first. When a parent entry fill and its attached child's
  exit fill were both new in one reconcile pass, the child's exit projected first as a phantom
  position, the parent's larger fill over-closed it, and the residual inventory was dropped.
- PostgreSQL inserts fill evidence before projecting it, so a pyramid add never counted toward
  `max_open_positions`.

## Decision

- **Entries are parentless maker or marketable orders.** An attached child or any protection
  kind is never a working entry.
- **An expired entry is always cancelled.** When the book may not reprice, the cancel is
  followed by the same abandonment as `on_unfilled_entry: cancel`. Executions the cancel
  reports are applied first, so their position keeps its stop; if the venue reports
  executions whose fills cannot be read yet, the book pauses with `Cancelled entry reported
  executions that are not readable yet; reconcile before resuming.` and keeps its pending stop
  and target for the reconcile that imports them.
- **A reprice that cannot go ahead abandons the entry instead of pausing.** No maker price, no
  ATR, a sizing skip, a non-breaker denial, or a replacement the venue rejected or cancelled at
  once (a post-only order that would have crossed) returns the book to flat with the entry
  cooldown; a pyramid add keeps its position and stops waiting. This is what a non-breaker risk
  denial already did. The decision journal is not written for it: the bar's own signal
  decides that. Breaker denials still pause. A first entry the venue cancels at once is
  abandoned the same way as a rejected one.
- **A missing maker price at entry time skips that bar** with entry block
  `MAKER_PRICE_UNAVAILABLE`; the next matching bar tries again. A book paused by the old
  detail is not resumed automatically.
- **A reprice's replacement uses the first entry's attach rule**, including that a live futures
  entry never attaches a bracket, and a futures reprice sizes with the same fee evidence as its
  first entry. A replacement the venue reports filled at once has its fills applied before the
  phase is set.
- **A live discretionary book without a live broker pauses** with `Live broker is unavailable.`
  (keeping any existing pause) before any maintenance; it never runs on the paper broker.
- **Fills project in execution order.** Before reconcile reads each order, it lists every watched
  order's fills and applies the unseen ones together by fill time (a parent's before its
  child's on a tie); the walk reuses those listings. While a parent's fills cannot be listed,
  its attached children's fills are held for that pass (their listing reads as failed), so a
  child's exit never projects against unknown entry economics. Unapplied stored fills replay
  by fill time. An exit fill larger than the
  open position still applies its exact cash, flattens the book and pauses it (a stopped book
  stays stopped) with `Exit fill exceeds the open position; the residual inventory is
  unmanaged.` instead of dropping the residual.
- **A pyramid add counts once per add order** on every store: only fills whose economics were
  already applied count as earlier fills of the order.

## Consequences

- Books no longer need a manual resume after a transient maker-price failure or a failed
  reprice; the decision journal records why the entry was skipped.
- The over-closing pause needs an operator: the venue holds inventory that no position
  describes. Reconcile at the venue before resuming.
- Pyramid caps now bind on PostgreSQL. A book whose stored `add_count` undercounted keeps its
  stored value; later adds count from there.

- Reconcile now lists a book's watched orders' fills before reading each order, so the broker
  request order changed. Replay packages captured before this change fail closed on that order
  (ADR 0137); the synthetic replay fixtures were regenerated.
- A discretionary pending entry whose entry reports executions not yet applied keeps its
  pending stop and target instead of returning to flat, so the late fills open a protected
  position.

## Alternatives considered

- Auto-clearing the maker-price pause on the next good read: rejected, because paused live
  books are never resumed automatically except for the narrow feed and data-gap cases.
- Projecting an over-closing residual as an opposite position: rejected; on spot an oversold
  residual is an accounting fault, not a short.
