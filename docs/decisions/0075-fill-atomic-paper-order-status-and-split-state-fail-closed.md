# ADR 0075: Fill-atomic paper order status and split-state fail-closed

## Status

Accepted

## Context

Paper deployment `01a0bb90-1eed-7b81-bea9-1826d2cf86c8` (UNI-USDC 1h Bollinger,
ops contract v36 build) matched its entry signal and `PaperBroker` produced a
fill, but `_match_resting_orders` (`src/thytrader/execution/loop.py`) persisted
the order as `filled` via a separate `save_order` before `ingest_fill` ran. When
fill ingestion failed, the order stayed FILLED forever with no fill row, no
position, and unchanged cash. FILLED is not in the `_ACTIVE` set, so
`_active_entry` returned None and `_manage_working_entry` never ran: the book
ticked in `pending_entry` for ~20 hours, never waited, never canceled after
`max_entry_wait_bars`, never placed a new entry, and operator
`reconciliation` reported healthy with `findings=[]` because no collector
looked at filled-without-fill or pending-entry-without-working-order state.
The paper stop and target remained parked on the runtime with no inventory.

This violated ADR 0057 (fill insert and cash/position projection commit
together) and the execution contract (PaperBroker produces Order + Fill +
Position atomically).

## Decision

1. **Fill-atomic order status.** `_match_resting_orders` no longer writes
   `save_order(FILLED)` before ingesting. The order `filled` status and
   `filled_quantity` are folded into `apply_fill_transaction` in both stores,
   committed in the same transaction as the fill row and economics. If
   ingestion fails, the order stays `open` and the next closed bar retries the
   match. Paper fill evidence and order status can no longer diverge across a
   crash or persistence failure. The folded quantity is
   `max(order.filled_quantity, applied fills + this fragment)`: callers on the
   reconcile path pass orders already carrying the venue-reported total, so the
   write preserves the venue total and per-fragment accumulation never
   double-counts quantity the order row already reflects.

2. **Split-state fail closed.** A `pending_entry` book with neither a working
   entry order (`_ACTIVE` status) nor a position is split state. The runtime
   pauses it with a mismatch detail instead of ticking silently forever
   (`split_pending_entry` in `execution/loop.py`, enforced in
   `_manage_position`). Already-paused books keep their protective-exit
   behavior. Fill economics are never invented to repair state.

3. **Operator reconciliation surfaces the split.** Reconciliation emits a
   `FILLED_WITHOUT_FILL` finding (FILLED order, no applied fill row, no
   position) or `PENDING_ENTRY_WITHOUT_ENTRY` (no working entry, no position)
   so `findings=[]` can no longer hide a stuck book. An empty operator finding
   list is not an all-clear for runtime invariants that reconciliation does not
   otherwise cover.

4. **Contract identity.** Ops contract bumps to `thytrader-ops-contract-v34`
   (Alembic stays `0046`; no schema change). `EXPECTED_SCHEMA_REVISION` is
   reserved for persistence-schema identity and does not track CLI/API behavior
   changes.

## Consequences

- Regression tests live in `tests/execution/test_loop.py` (ingest failure
  leaves the order OPEN; recovery after the fault clears; seeded split state
  pauses with a mismatch detail) and
  `tests/operator_diagnostics/test_service.py` (reconciliation finding).
- PostgreSQL coverage for `apply_fill_transaction` order-status persistence
  runs only with `THYTRADER_TEST_DATABASE_URL` set.
- Existing split-state books (for example `01a0bb90…`) are not auto-repaired;
  they pause on the next evaluated bar with a mismatch detail for operator
  review. Synthetic fills are never created to unstick them.

## Alternatives considered

- Reconcile-then-repair at read time: rejected; synthesizing a fill or
  position from order status alone would invent inventory.
- Leave the order FILLED and add a watch dog for missing fills only: rejected;
  the split begins at the write, so the write must be atomic (ADR 0057).
- Pause without a reconciliation finding: rejected; operators found the stuck
  book only through manual inspection, which is how it stayed stuck for ~20
  hours.
