# ADR 0058: Protection lifecycle, leases, and live capital accounting

## Status

Accepted

## Context

The 2026-09-16 external audit left protection, concurrency, and accounting defects after
[ADR 0057](0057-atomic-fill-ledger-and-product-isolation.md) shipped the atomic fill ledger,
per-product reconcile, `deployments.revision`, unused lease columns, and
`attached_child_venue_order_id`. Unverified parent stop/target geometry was still treated as
coverage (F04 leftover). Protective maintenance waited on signal-ready bars and returned early on
feed-down, HTF gap, and empty `due` (F05). Partial-entry remainders dropped out of exposure once a
position existed (F07). Lease columns were unused (F08 leftover). Stop canceled brackets and dropped
STOPPED residual occupancy (F09). Reprice skipped risk re-admission (F10). Live `_prepare_live`
overwrote strategy `cash` with venue available quote (F12). Daily-loss used realized-today plus
lifetime unrealized and reset with lifecycle (F13). Recovery could submit historical signals as new
live entries (F21). Discretionary entries accepted stale last-close marks (F22).

## Decision

1. **Verified attached coverage (F04).** `execution/attached.py` treats a missing, canceled, or
   rejected child as uncovered. Parent geometry is never coverage. HTTP/operator `protection_status`
   on `positions[]` and `books[]` ([ADR 0060](0060-multi-book-deployment-api.md)) calls
   `attached_entry_covers`: missing child is `unprotected`, matching child or resting exit is
   `covered`, and `unknown` is only an unreconciled protective order. Submit persists the child;
   reconcile imports child fills; the loop rests or replaces protection.
2. **Protection independent of entries (F05).** Reconcile and verified protection run on every poll,
   including feed-down, HTF gap, empty `due`, and discretionary books. Pause/breaker/gap disable new
   entries; they do not skip observing owned orders or escalating uncovered inventory.
3. **Partial-entry lifecycle (F07).** Working entries are tracked in any phase. Remainder expiry
   continues in `PENDING_EXIT`. Exposure is marked inventory plus every executable remainder.
   Reprice quantity is original minus filled.
4. **Fenced leases and revision (F08).** Workers acquire a 45s fenced lease. Worker writes use
   `save_deployment(expected_revision=...)`. Active identity uniqueness is
   `ux_deployments_active_strategy_mode` for running and paused strategy+mode rows. Intent
   idempotency keys are stable command/signal identities. Network I/O stays outside long DB
   transactions.
5. **Stop vs flatten (F09).** `LifecycleCommand` is `none` / `stop_new_entries` / `flatten` /
   `managed_shutdown`. HTTP/CLI stop defaults to managed shutdown: cancel risk-increasing entries,
   keep protective brackets, keep residual occupancy until settled. `--flatten` /
   `?flatten=true` marketably exits then cancels remainders. STOPPED residual books stay in
   account-level risk.
6. **Reprice re-admission (F10).** Every risk-increasing replacement re-runs sizing, exposure,
   breakers, collars, and the entry-rate gate. Paused and latched books must not reprice risk-up.
   Geometry preserves remaining qty and recomputes legal prices (drop an obsolete target below a
   new buy).
7. **Live capital vs cash (F12).** Venue available quote, allocated capital, reserved buying power,
   inventory cost, performance equity, and initial/baseline equity are separate columns. Ledger
   `cash` is never overwritten by venue quote. Unknown balances disable entries. Losing live
   ledgers keep a non-zero initial-equity baseline.
8. **Durable daily-loss and drawdown (F13).** Daily PnL is equity change from persisted UTC
   day-open (flow-adjusted by later stamps). High-water marks are durable. Latches persist until
   explicit reset. STOPPED residual and discretionary books participate in breaker eval.
9. **Recovery without historical live entries (F21).** Reconcile first. Replay past due bars with
   `allow_new_entries=False`. Only the latest still-valid signal may enter, with max signal age and
   current-quote checks. Event time and processing time are stored on the deployment.
10. **Discretionary freshness (F22).** Entry prerequisites centralize last expected close, candle
    age, product enablement, and connection/balance health. Marketable quote-budget orders require a
    fresh mark, not a day-old close.

## Consequences

- Alembic `0035_protection_lifecycle_accounting` adds lifecycle, capital, latch, and signal-time
  columns plus `ux_deployments_active_strategy_mode`.
- Ops contract becomes `thytrader-ops-contract-v22` with `expected_schema_revision` `0035` and
  `lifecycle_commands`.
- Operator `DeploymentSummary` reports `lifecycle_command` and breaker latches without cash or
  quantities. Runtime stop remains `--confirm`; live still `--i-understand-live`. Default stop is
  managed shutdown; flatten is explicit.
- Regression tests cover attached-child coverage, remainder exposure, leases/revision (including
  PostgreSQL), STOPPED residual occupancy, live capital vs cash, day-open PnL, replay age, and
  stale discretionary marks.

## Alternatives considered

- Inferring coverage from parent stop/target when the child id is missing: rejected; that is the
  F04 leftover.
- Treating STOPPED as operationally terminal: rejected; residual inventory must remain in
  aggregates until settled or transferred.
- Overwriting live `cash` with venue available quote for sizing: rejected; two strategies on one
  account would share and corrupt fill accounting.
