# 0141: Cancel time and a settlement watermark for canceled orders

- Status: Accepted
- Date: 2026-10-15
- Relates to: [0057](0057-atomic-fill-ledger-and-product-isolation.md),
  [0110](0110-stopped-lifecycle-reconciliation.md)

## Context

A `CANCELED` order stays in the reconcile watch set forever (ADR 0057 F06, ADR 0110): a cancel
acknowledgement is not a fill-visibility watermark, so every cycle reads the order and its fills
from the venue again, about two requests per canceled order per cycle, across running, paused
and stopped books. Each read also saves the order with a fresh `updated_at`. The cancel-rate
breaker counts canceled orders whose `updated_at` falls in the last minute, so it counts the
same old cancellations every cycle and can deny new entries with `CANCEL_RATE_LIMIT` after zero
real cancels. A failed read turns a canceled order into `UNKNOWN` and the next good read back
into `CANCELED`, which a timestamp of the last change would also count again.

## Decision

1. **Cancel time.** `Order.canceled_at` is set once, by the store, when an order is first saved
   as `CANCELED` (its `updated_at` then), and never cleared, including through an `UNKNOWN`
   read failure. Every writer goes through the store, so no call site can forget it. The
   cancel-rate breaker counts orders whose `canceled_at` is in the last minute.
2. **Settlement watermark.** A canceled order leaves the watch set once a successful venue read
   shows it `CANCELED`, the applied REST fills cover every executed quantity the venue reported
   for it, and at least 15 minutes have passed since `canceled_at`. Reconciliation then records
   `Order.settled_at` and stops reading it. Until all three hold, the order is read every cycle
   as before; a failed read never settles it. The delay covers the venue's eventual consistency
   between an order's terminal state and its published fills; the fill check is what makes a
   settled order's economics complete.
3. **Existing rows.** The real cancel time of orders canceled before this change is unknown, so
   they get their last save time, which is never earlier than the cancel: settlement still waits
   the full delay after the real cancel, and with no synthetic offset there is no clock-skew
   edge. Only an order saved in the minute before the upgrade can fall inside the cancel-rate
   window, for at most that minute, which is how the breaker counted it before. Orders a failed
   read left `UNKNOWN` after the venue reported them canceled (`terminal_read_unconfirmed`) are
   backfilled too, so their first good read after the upgrade is not counted as a new
   cancellation. The backfill uses only the application's timestamps, never the database clock.
4. **Replay.** Both fields are serialized only when set, so packages captured earlier keep their
   canonical bytes; replay restores captured orders exactly (no cancel time is invented) and
   compares a replayed canceled or unknown order without these fields when the captured order
   predates them. Settlement measures its delay at the read's own timestamp and takes no
   clock reading of its own, so earlier replay tapes still hold every clock input it uses.

## Consequences

- Venue reads fall to the orders that can still change: working, unknown, partially applied,
  and recently canceled orders.
- `CANCEL_RATE_LIMIT` counts real cancellations once.
- A settled order is final local evidence. A fill the venue publishes for it more than 15
  minutes after a consistent terminal read would not be learned; the venue-level account
  reconciliation remains the backstop for such a discrepancy.

## Alternatives considered

- **Count cancel requests instead of order states.** Misses venue-initiated cancels (expiry)
  and needs a separate event log.
- **Stop bumping `updated_at` on unchanged reads.** Still recounts a cancel after a failed-read
  round trip, and leaves the venue reads unbounded.
