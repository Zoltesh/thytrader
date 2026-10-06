# 0117: Truthful deployment inventory and explicit fleet controls

- Status: Accepted
- Date: 2026-10-06
- Relates to: [0058](0058-protection-lifecycle-accounting.md), [0060](0060-multi-book-deployment-api.md),
  [0061](0061-application-trust-boundary.md), [0064](0064-deployment-http-lifecycle-and-breaker-latch-reset.md)

## Context

`thytrader-runtime list` called `GET /api/v1/deployments` without paging arguments. The route
defaulted to 50 rows and did not say whether more existed. The Trade page used that same
unpaged call and could hide discretionary books past the first page. Inventory order was
`updated_at DESC`. A book supervised between page reads changed position, so offset pages
could skip or repeat deployments.

`show` defaulted to summary, which omits historical orders and fills, while the runtime
client docstring and one skill paragraph described those collections as present. Summary
ledger figures are aggregates, not a complete history.

Operators also had no explicit fleet-wide way to inhibit entries without pausing, to record
managed shutdown for a reviewed set of books, or to flatten only when that was the named
action. A kill switch that implies flatten can trap or exit positions the operator did not
choose. Entry inhibition has to survive process restart and must not block risk-reducing
exits.

## Decision

1. **Stable inventory snapshot.** List pages order by `created_at DESC, id DESC`. The response
   includes `returned`, `has_more`, `total`, `order`, `as_of`, `fingerprint`, and `next_cursor`.
   Complete readers follow keyset cursors, which bind the timestamp boundary, strategy filter,
   immutable last key, and identity/classification digest. Deletion or reclassification between
   page reads returns `inventory_changed`; the caller restarts or reports incomplete. Legacy
   offset pages remain explicit pages and cannot certify a complete fleet. The HTTP default limit remains 50 so existing callers do not silently receive
   a larger payload, but `has_more` makes truncation visible. `thytrader-runtime list` walks
   that snapshot to completion unless `--limit`/`--offset` request one page. `--all` is the
   explicit complete walk. A walk that cannot finish raises instead of printing a prefix as
   the fleet.
2. **Summary honesty.** `show` defaults to `detail=summary` and sets `ledger_omission` when
   historical orders and fills are absent. `--detail full` and read-only `orders` / `fills`
   pages are the history reads. List rows use the same summary omission label.
3. **Separate fleet actions.** `disarm` sets a durable per-mode entry latch and does not pause,
   cancel, or flatten. `stop` records managed shutdown only for confirmed `id:revision` pairs.
   `flatten` is a different route and records the existing flatten lifecycle command. `rearm`
   clears the latch and does not resume books. Live rearm and live-capable flatten require
   `i_understand_live`. All four mutations require `confirm=true`; YOLO does not cover them.
   The same idempotency key returns the durable result. PostgreSQL session advisory locking
   serializes same-key requests across instances, including existing pending intents. Local
   serialization bounds connection reservations but is not the correctness lock. Each book
   command uses existing lifecycle semantics, checks the confirmed revision inside the real
   `FOR UPDATE`/CAS mutation, and commits with its causal target receipt in one transaction. Partial failure is reported. Acceptance is
   not a venue fill and is not an atomic transaction across books.
4. **Entry latch.** New starts and entry intents are refused while the mode is inhibited.
   Non-entry intents are not. The execution worker refreshes a process snapshot each cycle;
   `entries_allowed` consults it so the existing loop skips new entries and risk-increasing
   reprices without a new broker path. The durable row lock is the start/intent backstop.
   Missing tables, rows, read failures, or an unloaded process cache fail closed for entry
   admission only. Disarm/rearm require `expected_inhibition` preview revisions for every scoped
   mode. Every deliberate latch command advances the revision, even if its bit is unchanged.
   Latch change and operation receipt commit atomically. Retries replay receipts, never a latch
   write that can overwrite a newer opposite choice. Operation `inhibition` is the historical
   receipt snapshot; read `GET /fleet-control` for current state.
   Admission linearizes on the mode row: a start/entry-intent transaction accepted before disarm
   may remain in flight after disarm, just as pre-existing open orders may. Disarm is not
   cancellation; it makes no impossible instantaneous-submission promise.
   Alembic `0067` adds the latch and operation log. This worktree chains it from `0064`; lead
   rechains it after `0065`/`0066`.

### Amendment 2026-10-06 — lead correctness review

This amendment replaces the initial offset/as-of completeness claim and fail-open pre-migration
behavior. A keyset continuation is checked against full identity/classification membership on each
page. The initial implementation's separate command/result and latch/result writes are replaced
by transactionally coupled receipts. Unknown interruptions leave durable progress pending; replay
uses receipts, not coincidental lifecycle state. No additional migration or venue execution path
is introduced. These corrections are release prerequisites, not evidence of deployment.

## Consequences

- Agents can see when a deployment page is incomplete and can list a stable snapshot without
  skipped or duplicate rows caused by `updated_at` changes.
- A summary show no longer looks like a book with zero historical orders.
- Disarm cannot be mistaken for flatten. Residual positions remain until an explicit flatten
  or an ordinary exit.
- A restart reads the same latch. Rearm is explicit and, for live scope, acknowledged.
- Ops contract identity is left for lead integration. `EXPECTED_SCHEMA_REVISION` matches this
  worktree's Alembic head so the head test passes; lead must reconcile it with the other
  reserved revisions.

## Alternatives considered

- Keep `updated_at` order and only add `has_more` — rejected; moving updates still skip rows.
- Make disarm pause every book — rejected; pause, cancel, and flatten must stay distinct.
- Call the broker from the fleet service and report one atomic success — rejected; venue calls
  are not one transaction, and exit logic stays in the existing worker.
- Block exits while disarmed — rejected; a kill switch must not trap a position.
