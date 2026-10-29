# 0149: A disproved reconcile read fault clears itself; paper orders are never read back

- Status: Accepted
- Date: 2026-10-28
- Amends: [0110](0110-stopped-lifecycle-reconciliation.md) (retained faults),
  [0141](0141-cancel-time-and-settlement-watermark.md) (the canceled-order watch set),
  [0146](0146-data-gap-pauses-clear-themselves.md) (scope)

## Context

A failed venue read during reconciliation pauses a book with `Order reconciliation is unconfirmed:
order read failed.` (or `…: fill read failed.`, `…: venue status is unknown.`) and marks the order
`UNKNOWN`. The walk then retains that detail on every later cycle: a retained fault is restored
even after the order reads back, so the only release was an operator resume. Books paused this
way after a brief venue outage kept a "read failed" detail for weeks after every order had read
back terminal and settled, re-observed as a `BOOK_PAUSED_MISMATCH` warning every cycle, and a
stopped book could never shed it.

Separately, the paper broker has no venue: its `get_order` answers `OPEN` for any id. Since ADR
0141 a canceled order stays in the reconcile watch set until a read settles it, so the worker's
reconcile of a paper desk (discretionary) book rewrote a take-profit that the stop's marketable
exit had just cancelled back to `OPEN`, and it rested again on a flat book.

ADR 0146 cleared decision-window data-gap pauses on the strategy path only; a desk book paused by
a gap never resumed.

## Decision

- **Disproved read faults clear, without resuming.** At the end of a reconcile walk that recorded
  no new fault, a book whose detail is `…: order read failed.` or `…: fill read failed.` has it
  cleared when, across the whole book (every product's orders, read from the accounting snapshot
  because a multi-instrument walk may run through one product's view), no order is `UNKNOWN`,
  every order that still needs a venue read was visited by this walk (a sibling product's order
  can leave `UNKNOWN` through a cancel acknowledgement without any successful read; it waits for
  a walk that reads it), every recorded execution is covered by applied fills (a cancelled order
  can report an executed quantity whose fills were never read) and no applied inventory is
  unprojected. Both failures mark their order `UNKNOWN`, so a repeat never counts as success. `…: venue status is unknown.` is not cleared: the venue answered,
  and fill ingestion can still mark that order filled while its status stays unknown. The status is
  unchanged: a paused book stays paused until the operator resumes it, a stopped book stays
  stopped. Audit `reconcile_read_fault_cleared` records the old detail. Every other detail
  (filled order without REST fills, unconfirmed submit, adoption evidence, ledger faults) is
  untouched.
- **Paper orders are local.** Reconciliation never asks the paper broker about an order that
  has a venue id; the store is its only source of truth. It only checks that the order's
  applied fills cover the quantity it recorded as executed (a paper fill is written after its
  order, so an interrupted submit can leave a filled order without its fill), and faults
  `Filled order has no REST fills.` otherwise, as a venue read would. The rule keys on the paper
  broker, not on the book's mode, so a replayed tape or a test venue still reads a paper-mode
  book. Paper canceled orders stay unsettled in the ADR 0141 sense, which no paper consumer
  reads. A paper order without a venue id (an interrupted submit) is still looked up as before
  and pauses fail-closed.
- **Desk books clear data-gap pauses.** A discretionary book applies the ADR 0146 clear after its
  due bars ran on a whole window (paper books evaluate them while paused; a paused live book is
  reconciled and kept protected first). The ADR 0146 rules are unchanged: only the exact
  decision-window detail, no lifecycle command, no breaker latch, and the evaluation must have
  reached the newest due bar. A gapped window now pauses only a running discretionary book, as
  on the strategy path: before, it overwrote any pause reason (a reconcile fault, say) with the
  data-gap detail, which the clear would then have resumed.

## Consequences

- A transient venue outage no longer leaves a permanent "unconfirmed" detail or alert; real
  unknowns (an order still unreadable or still `UNKNOWN`) stay fail-closed.
- Paused books do not restart trading on their own: clearing the detail is not a resume. A
  paused book whose read fault cleared shows as paused without a mismatch, like an operator
  pause; if the read fault had replaced a data-gap or user-feed detail, that pause no longer
  clears itself and the operator resumes it.
- A paper stop exit's cancelled take-profit stays cancelled, so paper desk books settle at once.

## Alternatives considered

- Auto-resuming a book once its read fault is disproved: rejected; a reconcile fault is a
  money-path pause and operators may keep such books paused deliberately.
- Teaching the paper broker local order state: rejected; the broker has no store access, and
  reading local truth back through a fake venue adds nothing.
