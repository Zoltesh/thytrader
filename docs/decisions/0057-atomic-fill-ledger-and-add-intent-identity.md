# 0057: Atomic fill-application ledger and add-intent identity

- Status: Accepted
- Date: 2026-09-16
- Relates to: [0019](0019-ops-contract-identity.md),
  [0031](0031-coinbase-first-platform-end-state.md),
  [0045](0045-spot-shorting-and-attached-entry-brackets.md),
  [0050](0050-daily-loss-drawdown-rate-collars.md),
  [0056](0056-multi-instrument-documents-and-pyramiding.md)

## Context

An external engineering audit of the commit pinned at `9fdacaf8` found five related
financial-correctness defects in fill handling, all rooted in the same design gap: a fill's
evidence (the row recording it happened) and its economic effect (cash, inventory, and order
progress) were written as separate, non-atomic steps.

- **Fill evidence and economic state can permanently diverge.** `_ingest_fills` called
  `store.save_fill` before applying the fill; position and deployment cash updates then committed
  separately. A crash between these writes left a fill recorded as "known" (so reconciliation
  never revisited it) without its cash or inventory effect ever applied. The same
  save-then-apply sequencing existed in paper resting-order matching.
- **Immediate live fills became synthetic zero-fee evidence, sometimes without a position.**
  `submit_intent` fabricated a `venue_order_id:immediate` fill with a zero fee and a local
  timestamp for *any* broker that returned `FILLED` on submit, live included. That synthetic
  quantity satisfied `_needs_reconcile`, so venue fills and real commissions were never fetched,
  and several call sites (`_submit_sized_entry`, `_ensure_take_profit`, `_reprice_entry`) never
  even applied that immediate fill to the position.
- **Timeout recovery applied the same discretionary fill twice.** On an `UNKNOWN` submission,
  the discretionary flow reconciled (which applied the remote fill), then unconditionally
  searched the book for "any `FILLED` order" and re-applied whatever fill it found — reproduced
  as a single 0.01-unit fill producing a 0.02-unit position and two cash debits.
- **Execution fragments exhausted a pyramiding database counter.** `Position.add_count`
  incremented on every same-side fill fragment and was constrained to 1–8
  (`ck_execution_positions_add_count`, [ADR 0056](0056-multi-instrument-documents-and-pyramiding.md)).
  An order that filled in more than eight fragments — legal even with pyramiding disabled —
  hit the CHECK constraint and left the position update silently dropped.
- **A failed follow-up GET discarded an acknowledged venue order ID.** The Coinbase adapter's
  `place_order` awaited `get_order` immediately after a successful POST and returned only that
  result; a transient GET failure raised past the caller, which then persisted a local order
  with no venue identity and had to rediscover it by paging historical orders for a matching
  client id.

## Decision

### One atomic, idempotent fill-application transaction (fixes F01, F03, F28)

Add `ExecutionStore.apply_fill_effect(FillApplication) -> FillApplicationResult`. Every caller
that turns one exact venue or paper fill into a durable economic effect goes through this single
method (via the new `thytrader.execution.fill_ledger` module), never through separate
`save_fill` + `save_order` + `save_position` calls:

- `execution_fills` gains `applied_at` (nullable). One transaction inserts the fill row
  (`ON CONFLICT` on the existing `(deployment_id, venue_fill_id)` unique constraint), applies the
  order/position/deployment cash effect, and stamps `applied_at`, or — when the fill's
  `applied_at` is already non-null — rolls back and reports `applied=False` without touching
  anything else. A crash at any point between "row exists" and "effect applied" is therefore
  either fully visible as unapplied (repair on retry) or fully applied; it cannot silently
  diverge. `InMemoryExecutionStore` and `PostgresExecutionStore` both implement this; the
  Postgres implementation holds one short `engine.begin()` transaction and performs no venue I/O
  inside it (venue GETs happen before this call, never inside the commit).
- `execution/loop.py`, `execution/reconcile.py`, and `execution/discretionary.py` all call the
  same `fill_ledger.apply_fill` (or `apply_or_import_fill`) instead of duplicating fill-application
  math. `reconcile.py::_ingest_fills` no longer calls `save_fill` before applying: recording and
  applying are the same atomic step. This closes F01.
- Discretionary post-submit processing (`_after_entry_submit`, `_marketable_discretionary_exit`)
  now takes the *exact* `Order` this submission produced — never "any `FILLED` order in the
  book" — and applies through the idempotent ledger. Even if a caller were to mis-scope an order,
  the ledger's `applied_at` guard makes a repeated application of an already-applied fill a
  verified no-op instead of a second cash debit. This closes F03 with two independent layers of
  protection: correct scoping, and idempotency as a backstop.
- `Position.add_count` now increments only when the fill's order intent differs from
  `Position.last_fill_intent_id` (a new nullable FK to `order_intents.id`). One order that fills
  across many partial fragments carries the same `intent_id` throughout, so it consumes exactly
  one pyramiding slot regardless of fragment count; a genuinely new add order intent consumes one
  more. The storage-layer `add_count` CHECK stays 1–8: it now enforces the same invariant as the
  strategy schema's `max_open_positions` bound (1–8, [ADR 0056](0056-multi-instrument-documents-and-pyramiding.md)),
  not a raw fragment count, so it is never hit by execution-fragment volume. This closes F28.
  `expected_schema_revision` in `src/thytrader/ops_contract.py` moves to Alembic `0034`
  (`0034_fill_applied_ledger_and_add_intent_identity`, revises `0033`; existing `execution_fills`
  rows backfill `applied_at` from `filled_at` since they predate the atomic ledger and were
  already applied by the prior code path). The `Fill` domain model carries `applied_at` so
  `_needs_reconcile` and live import skip only **applied** coverage. `apply_unapplied_fills`
  runs at the start of each closed-bar cycle and live reconcile so a crash after paper
  `save_fill` still repairs cash/inventory on restart.

### Paper simulation fills are not live evidence (fixes F02)

`submit_intent` fabricates an immediate `Fill` only when the bound broker is `PaperBroker`.
A live acknowledgement (including an immediate `FILLED` status on a post-only or marketable
order) is order progress only. `fill_ledger.apply_or_import_fill` is the single place that
decides which evidence to trust: if a local fill already exists for this exact order (the paper
path), it applies that; otherwise it calls `broker.list_fills` and applies each unseen real fill
and fee through the same atomic transaction. `_submit_sized_entry`, `_ensure_take_profit`,
`_reprice_entry`, and `_marketable_exit` (previously silent on an immediate `FILLED` result, or
only reachable in the discretionary book) now route every `FILLED` submission outcome through
`apply_or_import_fill` instead of ignoring it or trusting the acknowledgement's aggregate
quantity as evidence.

### Persist the POST acknowledgement independent of the GET (fixes F36)

`CoinbaseRestBroker.place_order` catches `BrokerError` raised by its own follow-up `get_order`
call and returns `SubmitResult(status=OrderStatus.UNKNOWN, venue_order_id=<known id>)` instead of
letting the exception propagate. `OrderStatus.UNKNOWN` was already a first-class watched status
(`_needs_reconcile`, `_WATCH`); it is deliberately not `OPEN` or `FILLED`, since the GET never
confirmed either. `submit_intent`'s existing `replace(order, venue_order_id=result.venue_order_id,
status=result.status, ...)` path persists that venue identity immediately, so reconciliation's
`broker.get_order(venue_order_id=order.venue_order_id or "", ...)` uses the known id directly and
never falls back to paging historical orders for a client-id match, and never re-POSTs.

## Consequences

- `ExecutionStore` implementations must implement `apply_fill_effect` as one atomic unit;
  `DisabledExecutionStore`, `InstrumentScopedStore`, `InMemoryExecutionStore`, and
  `PostgresExecutionStore` all do.
- `Position.add_count` semantics change from "fill fragment count" to "distinct filled add intent
  count." `ops_contract.pyramid_add_count_semantics` (always `"intent"`) makes this explicit to
  operators and agents; `OPS_CONTRACT_ID` moves to `thytrader-ops-contract-v22`.
- Live paper/live parity improves: paper fills stay locally synthesized (documented assumed fees);
  live fills and fees always come from Coinbase's fill ledger, never a local guess.
- This slice does not change risk-gate semantics, breaker behavior, discretionary request
  validation, or the strategy schema. It does not add a durable outbox table for protection
  actions: per-bar `_ensure_exit_protection` / `_ensure_live_bracket` already re-evaluate and
  repair resting protection every closed bar from durable position/order state, so a crash
  between "fill applied" and "protection rested" self-heals on the next cycle without a separate
  outbox.

## Alternatives considered

- **Full event sourcing (append-only fill event log, replay to derive state).** Rejected as
  disproportionate: a conventional relational state machine with one applied-marker column and
  one atomic transaction satisfies the audit's invariant (`inventory == applied fills`,
  `cash == baseline + signed fill cashflows`) without a projection-replay subsystem.
- **A durable transactional outbox table for protection actions.** Considered per the audit's
  cross-cutting guidance. Rejected for this slice: protection (take-profit / live bracket) is
  already re-evaluated and re-rested every closed bar from durable state, which is a simpler
  self-healing mechanism than an outbox with its own retry/dead-letter semantics for a system
  that already polls every bar. Revisit if a future slice needs sub-bar protection latency.
- **Raise or drop the `add_count` CHECK constraint outright.** Rejected: once `add_count` means
  distinct add intents, it is bounded by the strategy schema's `max_open_positions` (1–8) by
  construction, so the existing 1–8 CHECK remains a meaningful invariant, not a fragment-count
  trap.
