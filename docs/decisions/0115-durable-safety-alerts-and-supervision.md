# 0115: Durable safety alerts and execution supervision

- Status: Accepted; delivery grading amended by [0138](0138-live-alert-delivery.md)
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
increment `occurrences` instead of inserting another row. A check resolves only when a
complete observation of that exact `(code, subject)` proves the condition absent. Unknown
or explicitly partial snapshots, inconsistent occupied/empty inventory,
unavailable/settling/warming candles, failed reads, and subset inventories do
not prove recovery. Verified checks can recover independently of unknown checks. Explicit
complete deployment inventory can prove intentional removal; alert history survives deletion.

Each check has a persistent observation watermark (`operator_alert_checks`). Recording
findings and explicit clears is one transaction. Older/replayed observations cannot clear
newer failures or reopen after a newer recovery; a failure wins an equal-timestamp tie.
Timestamps identify the beginning of the evidence pass, not its late completion. Evidence
freshness is judged separately, at a time taken after the venue reads its evidence came from,
so a receipt read during the pass is never mistaken for future-dated. The futures observer
judges after its own account read; the safety supervisor judges after the cycle's reads but
before its own closed-candle reads, which carry no receipt freshness; the live CFM fleet pass
judges its minimum-contract probes and their verdicts after each scope's account, binding and
instrument reads, on the worker clock, while its alert watermark and `evaluated_at` stay at the
pass start (2026-10-28). Its collateral state keeps the pass start: that classification only
ages a snapshot, so the pass start is at most seconds lenient and never future-dates a receipt.

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
  This is a lease-timing check, not proof of successful venue reconciliation: even a fresh
  lease alone cannot certify protection or per-book safety.
- `WORKER_LEASE_STALE` when a running book's lease is expired or its age is unknown. Unknown
  is not process death and is not per-book safety. Implausibly future expiries are unknown,
  not verified freshness. Clock skew stays in the detail.
- `WORKER_BOOK_FAILURES` counts observed raised cycles since the last verified successful
  decision. Unknown/no-decision/warming/lease-skipped cycles do not fabricate recovery or
  reset persisted counters. A stopped book makes no decisions, so its cursor never advances:
  its verified work is a maintenance pass (exits, protection, reconciliation) that ran to
  completion, not lease-skipped, deferred or warming the window cache, with the book stopped
  before and after it. That pass resolves the alert with its own note ("recovered: a later
  worker cycle completed this stopped book's maintenance ...") (2026-10-28). Before, a failure on
  a stopped book stayed open forever. At the configured threshold (default 3), new entries pause
  with reason `WORKER_CONSECUTIVE_FAILURES` and command `STOP_NEW_ENTRIES`. The pause
  acquires the worker lease, re-reads eligibility/revision/strategy, and writes through
  `RevisionFencedStore`; any conflicting stop/pause/delete/revision loses the write safely.
  A threshold persisted before a crash is retried on restart even without a new error.
  Observing the persisted pause keeps its alert open without incrementing the error count.
  Exits, reconciliation, and later cycles continue. User pauses, unrelated mismatches, and
  breaker latches are never overwritten or auto-resumed.

`GET /api/v1/operator/alerts` and `thytrader-operator alerts` read the feed. The UI is
`/alerts`. Counts and overall status use the full open inventory; the bounded display
prioritizes critical rows and reports truncation. With `notify_provider=none` the feed still records rows and the report sets
`delivery_warning`. A configured log or webhook sender is used as-is; no webhook URL is
invented or returned. Dispatch runs in a separate worker task, never inline with safety
processing. Each attempt is claimed durably before sending (60s expiry; 15s send timeout),
consuming one slot under `alert_delivery_max_attempts` (default 5). Concurrent dispatchers
cannot send the same active claim, and stale acknowledgements cannot overwrite newer
claims. Disabling persists `skipped` without consuming attempts; enabling can deliver an
existing skipped alert. Provider callbacks/result details cannot leak destinations or secrets.

An ambiguous send/ack crash can cause duplicate webhook delivery. Bounded retry can
also exhaust without delivery, so neither exactly-once nor eventual external receipt is
guaranteed. `NotificationRecord.id` is the stable id of one page on every retry: the alert id
for a row's first page, and `uuid5(alert id, "severity:<severity>")` for a later page of a row
that already carries `covered_severity` (an escalation). Receivers must idempotently dedupe it. Successful acknowledgements are not redelivered. Delivery
bookkeeping never changes safety observation timestamps.

Thresholds are optional settings with explicit defaults:
`alert_consecutive_failure_cycles` (3), `alert_decision_missed_bars` (2),
`alert_delivery_max_attempts` (5, per page), `alert_entry_block_grace_seconds` (300: a
`FLEET_ENTRIES_BLOCKED` alert is recorded at once but delivered only after it has stayed open
that long, so a self-healing one-poll venue blip is not paged; entry blocks never stop exits;
added 2026-10-23). The grace counts from when the fleet read began (the start of its evidence pass), not from
cycle start, so a fleet read slower than the grace can still page a blip,
and a block that returns within the grace of its last resolution continues one episode: a
flapping block is paged once its episode has lasted the grace, and its later returns are not
paged again (a return the delivery task sees is recorded as skipped) (2026-10-24). The episode
is read from that block's own resolved rows (newest 500), never the bounded operator feed. A
page covers returns of the same or lower severity, so an escalation to critical is paged; a
return waits while an earlier send is in flight, and a send acknowledged after its row resolved
still counts. Coverage is the severity actually sent (`covered_severity`, Alembic `0081`; a
continuation skip inherits its page's), never the row's later observed severity, so a block
that escalated in place after a warning page is paged when it returns, and one downgraded after
a critical page is not. A row sent before `0081` covers nothing. A walk of 500 rows that finds
no gap or covering page while the next older row still joins the episode is due at once,
since the episode's start lies beyond the window.
An episode with more than 500 returns (for example a fast flap, or returns the delivery task
never saw while it was down), or one spanning the `0081` upgrade, may be paged early or a
second time: a duplicate, never a missed page.

An open row that escalates in place after its page (any code) is paged again at once
(2026-10-28). Dispatch re-arms it with a compare-and-set that matches only an open, sent
(`logged`/`delivered`), unclaimed row still at the observed severity whose `covered_severity` is
the lower one read: it becomes `pending` with a fresh attempt budget, so concurrent dispatchers
re-page it once, and the ordinary claim and the entry-block episode rules then apply. The page
carries its own id (above) and says it escalated. A send in flight when the row escalates
acknowledges the lower coverage, so the next dispatch re-pages. A downgrade is never paged, a
failed escalation page keeps one bounded budget (re-arming starts only from a sent row), and a
row sent before `0081` has no known coverage and is not re-paged in place.

All four thresholds are environment-only (`THYTRADER_ALERT_CONSECUTIVE_FAILURE_CYCLES`,
`THYTRADER_ALERT_DECISION_MISSED_BARS`, `THYTRADER_ALERT_DELIVERY_MAX_ATTEMPTS`,
`THYTRADER_ALERT_ENTRY_BLOCK_GRACE_SECONDS`) and Compose passes each to the services with the
same default (2026-10-28).

Alembic `0066` adds `operator_alerts` (including delivery claim token/expiry) and
`operator_alert_checks` (monotone timestamps and failure-wins tie state). This checkout's parent is `0064` because `0065` is
not present. Expected schema revision is `0066` (at the time of this slice; later migrations
move it, see `EXPECTED_SCHEMA_REVISION`).

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
