# 0053: Trade-reason journals

- Status: Accepted
- Date: 2026-09-16
- Relates to: [0012](0012-operator-diagnostics.md), [0013](0013-http-first-agent-clients.md),
  [0019](0019-ops-contract-identity.md), [0030](0030-agent-e2e-primary-surface.md),
  [0033](0033-phase-10-risk-policy-registry.md),
  [0037](0037-phase-14-experiential-memory.md),
  [0039](0039-on-demand-discretionary-trades.md),
  [0046](0046-shipped-vs-remaining-0031-destination.md),
  [0047](0047-wider-fail-closed-indicator-catalog.md),
  [0048](0048-paper-deploy-fee-fields.md),
  [0049](0049-experiential-train-v1.md),
  [0050](0050-daily-loss-drawdown-rate-collars.md),
  [0051](0051-in-app-operator-chat.md),
  [0052](0052-richer-sweep-axes-study-catalog.md)

## Context

Phase 14 shipped origin-attributed journals, sentiment/pattern hooks, monitor, and notify
([ADR 0037](0037-phase-14-experiential-memory.md)). Those rows are facts and lessons. They are not
a per-order-intent record of **why a trade was made**.

Destination product (workstation destination, 2026-09-16) is that a human or an agent can open a
trade and see the published strategy version, closed-bar signal facts actually used, the risk
verdict, a discretionary note, and fill/reconcile facts. Same payload for UI review and operator
reports/skills. No interpolated candles. `--confirm` and `--i-understand-live` stay as they are.

[ADR 0049](0049-experiential-train-v1.md) already ships the fail-closed trainer. That trainer
consumes `JournalEntry` as stored. This slice owns why-trade review; it does not feed
`TradeReasonRecord` into training.

[ADR 0052](0052-richer-sweep-axes-study-catalog.md) shipped richer sweep axes and the persisted
research-study catalog. This ADR does not rewrite that catalog. Extra exchanges stay parked.

[ADR 0046](0046-shipped-vs-remaining-0031-destination.md) restated shipped vs remaining 0031
destination. [ADR 0047](0047-wider-fail-closed-indicator-catalog.md) shipped the wider fail-closed
indicator catalog. [ADR 0048](0048-paper-deploy-fee-fields.md) shipped paper deploy maker/taker
assumptions. [ADR 0050](0050-daily-loss-drawdown-rate-collars.md) shipped daily-loss / drawdown /
rate / collars. [ADR 0051](0051-in-app-operator-chat.md) shipped in-app operator chat. This ADR
does not rewrite those.

## Decision

Ship durable, attributed, redacted why-trade records as `thytrader-trade-reason-v1`, one row per
persisted order intent.

### Record

Frozen at intent persist:

- origin (`human` / `agent` / `runtime`) from the order intent
- published strategy identity (id, fingerprint, name, version) for strategy books; omitted for
  discretionary books
- signal kind (`strategy_entry`, `discretionary`, `take_profit`, `stop`, `time_exit`, `bracket`),
  `last_signal`, and the closed bar `candle_starts_at` actually used — never reconstructed candles
- risk-registry decision, reason code, detail, policy fingerprint, and compiled vs published source

Joined on read from the execution ledger (not stored on the why-trade row):

- order id/status, filled quantity, reject reason, unknown-timeout flag, exact fills

Notes are append-only attributed bodies (`human` or `agent`). An optional place-order `--note` is
the first note. Later notes use `thytrader-memory add-trade-reason-note --confirm`. Runtime cannot
author notes. YOLO never covers that mutation.

Denied risk with no persisted intent is not recorded. Recording is best-effort: a memory-store
failure never blocks order persist or broker submit.

### Surfaces

The same `TradeReasonRecord` payload is returned by:

- `GET /api/v1/memory/trade-reasons` and `GET /api/v1/memory/trade-reasons/{intent_id}`
- `POST /api/v1/memory/trade-reasons/{intent_id}/notes` (`--confirm` on the CLI)
- `GET /api/v1/operator/trade-reasons` / `uv run thytrader-operator trade-reasons`
- composable workstation review on Memory and Trade (not a third nav dump)

### Persistence and ops contract

Alembic `0032` creates `trade_reason_records` unique on `intent_id` and revises study-catalog
`0031`. Ops contract becomes `thytrader-ops-contract-v20` with `trade_reason_journals`
`paper`/`live` and `expected_schema_revision` `0032`. Paper fee fields, experiential-model engines,
breaker / rate / collar fields, and the persisted research-study catalog stay on the contract.

## Consequences

- Operators and agents can review why a paper or live intent was persisted without scraping logs
  or interpolating candles.
- Phase 14 journals remain facts/lessons. Why-trade rows are a distinct schema.
- The ADR 0049 trainer still consumes `JournalEntry` only. This slice owns why-trade review.
- Extra exchanges stay out.
- `--confirm` on memory notes and `--i-understand-live` on live place-order are unchanged.

## Alternatives considered

- **Compose why-trade only from existing intents:** rejected; risk verdict, strategy name/version,
  and discretionary notes are not on the intent row.
- **Rewrite the fill ledger with origin:** rejected; origin already applies to memory artifacts;
  fills stay exact ledger facts joined on read.
- **Block the order path when memory storage fails:** rejected; financial persist outranks the
  review document. Recording fails open.
- **Feed `TradeReasonRecord` into the trainer:** rejected; [ADR 0049](0049-experiential-train-v1.md)
  consumes `JournalEntry` as stored. This slice owns review, not training.
- **Add extra exchanges:** rejected; Coinbase Advanced Trade spot only.
