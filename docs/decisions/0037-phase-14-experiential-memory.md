# 0037: Phase 14 experiential memory, monitor, and notify

- Status: Accepted
- Date: 2026-09-15
- Relates to: [0012](0012-operator-diagnostics.md), [0013](0013-http-first-agent-clients.md),
  [0019](0019-ops-contract-identity.md), [0030](0030-agent-e2e-primary-surface.md)

## Context

Agent E2E is the primary product surface ([ADR 0030](0030-agent-e2e-primary-surface.md)). Shipped
evidence (backtests, paper/live ledgers, audit events) did not let operators or agents record
origin-attributed facts and lessons, inspect a composite watch of deployments plus those notes, or
request a user notification without inventing a private store.

Roadmap Phase 14 is that slice: journals, sentiment and pattern-learning **hooks**, monitor, and
config-gated notify. It is not a substitute for audit trails or immutable research results. It is
not ML training, venue sentiment scrape, on-demand SL/TP, `1m`/`2h` clocks, extra exchanges, or
Phase 13 live extras.

YOLO ([ADR 0034](0034-phase-12-agent-orchestration-yolo.md)) may skip `--confirm` on `data`,
`research`, and `paper` only. Memory mutations must not inherit that skip.

## Decision

Ship Phase 14 as a fifth confirmation-gated skill lane plus a read-only operator monitor report.

### Schema

`thytrader-experiential-memory-v1` records are append-only and frozen after validation:

- **origin** is required: `human` or `agent`
- journals: `fact` | `lesson` | `note` (lessons require `lesson_outcome` other than `none`)
- sentiment snapshots: operator- or agent-submitted labels only
- pattern observations: named `pattern_key` hooks with hypothesized/supported/contradicted/retired
- notification attempts: persist even when delivery is skipped

Optional evidence pointers name immutable artifacts (`backtest`, `paper_fill`, `live_fill`,
`deployment`, `research`, `market_data`). They do not replace those artifacts.

### Persistence and ops contract

Alembic `0023` creates the four tables and allows audit category `memory`. Ops contract becomes
`thytrader-ops-contract-v9` with `expected_schema_revision` `0023`. Writes fail closed when
PostgreSQL is unconfigured.

### Notify

Settings:

- `THYTRADER_NOTIFY_PROVIDER` — `none` (default), `log`, or `webhook`
- `THYTRADER_NOTIFY_WEBHOOK_URL` — required only for `webhook`; redacted everywhere else

Tests inject fakes and never open a network socket. Default-off notify is `skipped`, not `failed`.
Monitor treats only `delivery_status=failed` as `NOTIFICATION_FAILED`.

### Skill lane

`thytrader-memory` is not an extension of operator, data, research, runtime, or playbook. Mutations
require `--confirm`. YOLO never covers this lane (`hard_gate=True`). The CLI is HTTP-first against
`/api/v1/memory`. Operator `monitor` is read-only (`GET /api/v1/operator/monitor` and
`GET /api/v1/memory/monitor`).

## Consequences

- Agents can journal origin-attributed facts and lessons, record sentiment/pattern hooks, watch
  deployments plus recent memory, and request a notification without placing orders.
- Webhook URLs stay in Settings and `configured_secrets`; reports expose `notify_webhook_configured`.
- No model training, no venue scrape, no fill-ledger rewrite with origin.
- Phase 13 live extras, on-demand SL/TP, and `1m`/`2h` clocks stayed out of this slice. Those later
  shipped in ADRs 0036, 0039, 0040, 0045, and 0046. No model training in this slice.

## Alternatives considered

- **Fold journals into playbook/YOLO:** rejected; YOLO never grants memory mutations by inheritance.
- **Treat default-off notify as monitor failure:** rejected; skipped delivery is the documented
  default.
- **Rewrite fill ledgers with origin:** rejected; origin applies to memory artifacts only.
- **Train a model in this slice:** rejected; hooks are append-only observations, not a learner.
