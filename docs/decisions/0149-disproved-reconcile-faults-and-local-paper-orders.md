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
  no new fault, a book whose detail is one of the three read-failure details has it cleared when
  every order is out of `UNKNOWN` (read back this walk, or already terminal and accounted for),
  every stored fill's economics is applied and no applied inventory is unprojected. A repeated
  identical failure leaves the order `UNKNOWN`, so it never counts as success. The status is
  unchanged: a paused book stays paused until the operator resumes it, a stopped book stays
  stopped. Audit `reconcile_read_fault_cleared` records the old detail. Every other detail
  (filled order without REST fills, unconfirmed submit, adoption evidence, ledger faults) is
  untouched.
- **Paper orders are local.** Reconciliation never reads a paper order that has a venue id; the
  store is its only source of truth. Paper canceled orders therefore stay unsettled in the ADR
  0141 sense, which no paper consumer reads.
- **Desk books clear data-gap pauses.** A discretionary book applies the ADR 0146 clear after its
  due bars ran on a whole window (paper books evaluate them while paused; a paused live book is
  reconciled and kept protected first). The ADR 0146 rules are unchanged: only the exact
  decision-window detail, no lifecycle command, no breaker latch, and the evaluation must have
  reached the newest due bar.

## Consequences

- A transient venue outage no longer leaves a permanent "unconfirmed" detail or alert; real
  unknowns (an order still unreadable or still `UNKNOWN`) stay fail-closed.
- Paused books do not restart trading on their own: clearing the detail is not a resume.
- A paper stop exit's cancelled take-profit stays cancelled, so paper desk books settle at once.

## Alternatives considered

- Auto-resuming a book once its read fault is disproved: rejected; a reconcile fault is a
  money-path pause and operators may keep such books paused deliberately.
- Teaching the paper broker local order state: rejected; the broker has no store access, and
  reading local truth back through a fake venue adds nothing.
