# 0115: Durable safety alerts and execution supervision

- Status: Accepted
- Date: 2026-10-05
- Relates to: [0058](0058-protection-lifecycle-accounting.md), [0091](0091-portfolio-deployment-limits-and-manager-proposals.md), [0104](0104-bounded-newest-candle-wait.md)

## Context

Pause, breaker, uncovered-stop, and worker-failure conditions were visible only by scraping
logs or reading deployment rows. A late 6h or daily candle could be mistaken for a stale book
if supervision used wall-clock age instead of that clock's close plus the publication grace.
Notification delivery was optional and default-off, so a local operator still needed a feed
that works when `notify_provider=none`.

## Decision

The execution worker records durable, deduplicated operator alerts after each cycle, including
when signal evaluation raises. One open row exists per `(code, subject)`. Repeat cycles
increment `occurrences` instead of inserting another row. When the condition clears, the row
is resolved; a later recurrence opens a new row.

Alert codes:

- `BOOK_PAUSED_MISMATCH` for a fail-closed mismatch that is not a portfolio breaker or the
  supervision pause itself
- `BREAKER_LATCHED` for daily-loss, drawdown, and portfolio breaker pauses
- `STOP_UNCOVERED` and `STOP_COVERAGE_UNKNOWN` from verified protection classification. A
  live book whose only resting closing-side stops have all triggered without filling has no
  verified cover either, so `STOP_UNCOVERED` fires alongside the per-order alert below;
  any still-working closing-side order keeps the book covered. Paper stops stay covered
  because the worker enforces them synthetically on every closed bar.
- `STOP_TRIGGERED_UNFILLED` when a resting stop's trigger traded through on a closed bar and
  the order is still unfilled. Supervision does not submit a market order.
- `DECISION_DEADLINE_MISSED` only for a running book, on that book's clock, after ADR 0104's
  120-second settling grace. A 6h or daily boundary inside the grace is not stale.
- `MAINTENANCE_DEADLINE_MISSED` when an occupied book's worker lease is stale or unknown.
  A fresh lease means reconcile and protection ran, even if the decision cursor is waiting.
- `WORKER_LEASE_STALE` when a running book's lease is expired or its age is unknown. Unknown
  is not process death and is not per-book safety. Clock skew stays in the detail.
- `WORKER_BOOK_FAILURES` counts consecutive raised cycles. At the configured threshold
  (default 3) new entries pause with reason `WORKER_CONSECUTIVE_FAILURES`. Exits,
  reconciliation, and later cycles continue. User pauses, existing mismatches, and breaker
  latches are not overwritten and are never auto-resumed.

`GET /api/v1/operator/alerts` and `thytrader-operator alerts` read the feed. The UI is
`/alerts`. With `notify_provider=none` the feed still records rows and the report sets
`delivery_warning`. A configured log or webhook sender is used as-is; no webhook URL is
invented or returned. Failed deliveries retry only while the alert is open and under
`alert_delivery_max_attempts` (default 5).

Thresholds are optional settings with explicit defaults:
`alert_consecutive_failure_cycles` (3), `alert_decision_missed_bars` (2),
`alert_delivery_max_attempts` (5).

Alembic `0066` adds `operator_alerts`. This checkout's parent is `0064` because `0065` is
not present. Expected schema revision is `0066`.

## Consequences

- Operators can see safety issues from the CLI, HTTP report, or `/alerts` without scraping logs.
- A disabled notifier is an explicit warning, not a silent drop and not a failed install.
- Lead integration must re-point `0066.down_revision` if another slice lands `0065` first,
  and must reconcile `EXPECTED_SCHEMA_REVISION` if several slices bump it.

## Alternatives considered

- Log-only alerts: rejected; they do not survive restart and flood every cycle.
- Auto market escalation of an unfilled stop: rejected; that is a new order authority.
- Treating worker process liveness as per-book safety: rejected; a live process can still
  hold a stale lease on one book, and an unknown lease age is not proof of either.
