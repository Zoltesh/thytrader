# ADR 0057: Atomic fill ledger and product isolation

## Status

Accepted

## Context

The 2026-09-16 external audit (F01–F04, F06, F08, F27, F28, F36) found that fill evidence and
economic state could diverge across crashes, live submissions invented zero-fee `:immediate`
fills, multi-product reconciliation used the primary book, execution fragments incremented
pyramiding `add_count`, and POST acknowledgements were lost when follow-up GETs failed.

## Decision

1. **Atomic fill application (F01).** Introduce `execution/fill_ledger.py` with
   `ingest_fill` / `replay_unapplied_fills`. PostgreSQL and in-memory stores implement
   `apply_fill_transaction` so fill insert and cash/position projection commit together.
   `execution_fills.economics_applied_at` marks applied economics; reconciliation replays rows
   where it is null.

2. **Live vs paper fills (F02).** `submit_intent` records synthetic immediate fills only for
   `PaperBroker`. Live economics always arrive through venue fill ingest.

3. **Double-apply guard (F03).** Discretionary post-submit paths skip `apply_fill` when
   `economics_applied_at` is already set.

4. **Attached child tracking (F04).** Parent orders persist `attached_child_venue_order_id`;
   `_attached_entry_covers` requires a reconciled child before inferring protection.

5. **Cancel/fill races (F06).** `CANCELED` orders remain in the reconcile watch set until
   `fill_economics_complete` is true.

6. **Revision-checked writes (F08).** `deployments.revision` increments on each
   `save_deployment`; optional `expected_revision` rejects stale writers. Worker lease columns
   are reserved for fenced ownership (enforced in a follow-up slice).

7. **Per-product reconciliation (F27).** `reconcile_open_orders` scopes each order to its
   `product_id` via `InstrumentScopedStore` / overlay snapshots.

8. **Fragment-safe pyramiding (F28).** `add_count` increments only on the first applied fill
   from a distinct order, not on every execution fragment.

9. **POST acknowledgement retention (F36).** `CoinbaseRestBroker.place_order` returns
   `UNKNOWN` with the venue id when GET fails after a successful POST.

## Consequences

- Alembic `0034_atomic_fill_ledger` adds `economics_applied_at`, `revision`, lease columns,
  and child-order links.
- Regression tests live in `tests/execution/test_fill_ledger.py`; PostgreSQL coverage uses the
  same `apply_fill_transaction` path when `THYTRADER_TEST_DATABASE_URL` is set.
- Operator skills note that deployment snapshots may expose `revision` for diagnostics; no HTTP
  contract version bump is required for this slice.

## Alternatives considered

- Event-sourced ledger only: rejected for this slice; transactional projection with applied
  markers is sufficient and smaller.
- Removing `add_count` constraint: rejected; strategy pyramiding caps remain; only fragment
  counting semantics change.
