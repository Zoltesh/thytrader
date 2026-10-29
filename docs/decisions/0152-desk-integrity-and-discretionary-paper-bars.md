# 0152: Desk rows stay readable, desk books are valued from complete fills, and paper discretionary bars match strategy bars

- Status: Accepted
- Date: 2026-10-29
- Relates to: [0083](0083-unified-backtest-model.md), [0115](0115-durable-safety-alerts-and-supervision.md),
  [0147](0147-agent-desk.md), [0148](0148-live-agent-desks.md)

## Context

A review of the agent desk and discretionary paths found ways one bad value could stop every
desk, a live desk could start trading again unacknowledged, and paper desk results came out
better than the strategy engine would score the same trades:

- Settlement credited a book's value to the pool with a model copy, which skips validation. A
  book that lost more than its allocation (a paper short through a gap) wrote negative `cash`,
  which `cash >= 0` then rejected on every read. PostgreSQL's desk listing raised for every
  desk, and the worker's desk pass, the only place a failing or stalled desk becomes visible,
  failed quietly every cycle.
- Resuming a paused live desk needed no live acknowledgement, unlike creating it or changing
  how it trades.
- Desk placement ran without the risk market-data scope that the worker binds, so a peer book
  holding inventory overnight left the daily-loss evidence unknown and refused every desk entry
  (and every manual discretionary ticket) until it was flat.
- The live desk reservation check counted the requested notional, and sizing rounds it down,
  so the rounding remainder escaped the reservation.
- A refused open journaled a spurious "interrupted open was refunded" row after the refusal.
- A desk book's value counted only recorded fills, so an order the venue reported filled
  before its fills were imported valued (and could settle) the book at its whole allocation.
- `STOP_TRIGGERED_UNFILLED` read every resting order carrying a stop price. An unfilled entry
  carries its bracket's stop as metadata, so a resting buy limit raised a false triggered-stop
  alert (critical on a live book).
- Paper discretionary bars matched a resting take-profit without the stop-first rule, matched a
  fresh limit against the bar that closed before it was placed, and never refreshed the
  high-water mark.

## Decision

- A desk's `cash` may go below zero: it records a loss past the pool. Such a desk funds nothing
  (`DESK_CASH_SHORT`, as before for an empty pool) and stays readable.
- Every revisioned desk save validates the desk row, books and wakes it writes and refuses
  `DESK_INVALID` before storing anything.
- The desk list leaves out a stored row that fails validation, so one bad row never hides the
  others. One scan of every row yields both the readable desks and the malformed rows, and the worker raises `DESK_UNREADABLE` (scope `desk`,
  critical when the row says live or cannot say, warning for paper) until the row reads again.
- Resuming a live desk (`status active`) needs `i_understand_live` (HTTP 428 without it).
  Pausing and retiring reduce risk and never need it.
- Discretionary placement binds the risk market-data scope for the whole placement.
- A desk order carries its whole allocation; the reservation check counts the other books plus
  that allocation, whatever the sizing rounds.
- An open refused while placing journals its refusal in the refund's own write (one row, never a
  refund without its journal); an interruption with no refusal still journals
  `DESK_OPEN_REFUNDED`.
- A desk book's value is unknown while any of its orders reports more executed quantity than
  its recorded fills.
- An entry (a parentless order placed under an entry intent) never raises
  `STOP_TRIGGERED_UNFILLED` or counts as a stop in cover evidence; an open triggered-stop alert on
  any other order resolves without waiting for the book's economics.
- Paper discretionary bars use the stop-first rule of ADR 0083, a fresh book's evaluation
  cursor starts at the newest bar closed when it was placed (never older than its mark bar), a
  bar at or before the cursor is maintenance only (no matching, no cursor rewind), an order is
  never matched against a bar that closed before it was created, and
  each paper bar refreshes performance (equity, high-water mark, day-open) like a strategy bar;
  live discretionary performance is left as it was.

## Consequences

- Negative pool cash is visible in `show`, the briefing and the score instead of breaking them.
- An operator resuming a live desk by CLI passes `--i-understand-live`.
- Paper desk results lose the optimistic same-bar take-profit and pre-submission limit fills.
- One more alert code (`DESK_UNREADABLE`) in the operator feed; the ops contract moves to v113.
