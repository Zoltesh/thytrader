# 0039: On-demand discretionary trades with SL/TP

- Status: Accepted
- Date: 2026-09-15
- Relates to: [0004](0004-safe-execution-and-access.md), [0013](0013-http-first-agent-clients.md),
  [0019](0019-ops-contract-identity.md), [0030](0030-agent-e2e-primary-surface.md),
  [0031](0031-coinbase-first-platform-end-state.md), [0033](0033-phase-10-risk-policy-registry.md),
  [0036](0036-phase-13-live-extras.md), [0038](0038-complete-only-1m-2h-4h-datasets.md),
  [0040](0040-venue-strategy-paper-live-htf-clocks.md)

## Context

Coinbase-first destination includes on-demand (discretionary) trades with stop loss, take profit,
and other execution params, not only strategy-driven orders
([ADR 0031](0031-coinbase-first-platform-end-state.md)). A discretionary action must create an
**order intent**, pass the risk-policy registry, persist that intent before submit, use a unique
client order id, and treat a timeout as ambiguous (reconcile before retry). It must not call
Coinbase from the agent or UI client.

Phase 13 already placed native live OCO brackets and paper synthetic SL/TP after **strategy** entry
fills. Phase 14 journals must not become a fill-ledger origin rewrite. Dataset clocks from
[ADR 0038](0038-complete-only-1m-2h-4h-datasets.md) stay ingest-only. This slice is the missing
discretionary book.

## Decision

Ship long-only on-demand entries through the existing order-intent → risk → broker path.

### Discretionary book

A discretionary runtime is a `deployments` row with `kind = discretionary`. It is not bound to a
published strategy: `strategy_fingerprint` and `strategy_id` are null. `timeframe` is `1h` or `5m`
and is the closed-bar clock used for paper matching and synthetic stops (same legal execution
clocks as strategy paper/live). Live 5m still pauses when the user-order feed is down.

At most one occupied (running or paused) discretionary book exists per product and mode. A flat
running book may be reused. An in-market or paused book is a conflict. Stopped books stay
historical.

### Entry and exits

- Side is buy-to-open only. Shorting stays out of this slice.
- Entry kind is `post_only_limit` (default) or `marketable`. Limit price is required for maker
  entries. Marketable entries size and validate SL/TP against the latest closed mark; they do not
  invent a mid-bar price.
- Stop loss and take profit are required, quantized to the product increment, and must satisfy
  `stop < entry_or_mark < take_profit`.
- Quantity or quote notional is required (exactly one). Sizes quantize to venue increments and
  fail closed below min size.
- After an entry fill, exits reuse Phase 13 machinery: live rests one `trigger_bracket_gtc`;
  paper rests a post-only take-profit and fires a marketable stop when a closed bar trades through
  the stop. Trailing ATR and strategy time-exits are not applied (no strategy ATR).
- Paper never submits `trigger_bracket`.

### Risk, idempotency, origin

Entries (including book creation) evaluate `thytrader-risk-policy-v1` before intent persist: product
allowlist, running slots, open-position slots, paper capital, and portfolio/product exposure.
Exits are not gated. When allocations are nonempty, discretionary orders are denied — allocations
are a published-strategy allowlist.

`idempotency_key` is required and unique. A retry returns the existing snapshot and must not call
`place_order` again.

Intent `origin` is `human` or `agent` for discretionary rows and `runtime` for strategy-driven
intents. Origin is not written onto the fill ledger.

A timeout or transport failure persists `unknown` and runs GET-order reconcile. The runtime pauses
when the venue id is still missing. It never retries create-order for that client id.

### Surfaces

- `POST /api/v1/discretionary-orders` places one long. Live still requires configured credentials.
- `thytrader-runtime place-order` is confirmation-gated (`--confirm`; live also
  `--i-understand-live`). YOLO may skip `--confirm` only for paper. Live place-order is a hard gate.
- The Trade UI is a human origin surface over the same HTTP contract.
- Operator `runtime` / `strategies` summaries include `kind` and optional strategy identity.

### Persistence and ops contract

Alembic `0025` (after dataset `0024`) adds `deployments.kind` / `timeframe`, nullable strategy
identity for discretionary rows, and `order_intents.origin` / `idempotency_key`. Ops contract
becomes `thytrader-ops-contract-v11` with `expected_schema_revision` `0025`. Strategy, paper, and
live clocks stay `1h` or `5m` in this ADR.
[ADR 0040](0040-venue-strategy-paper-live-htf-clocks.md) later widened those clocks; this slice does
not change intent persistence, risk, OCO, or reconcile-before-retry.

## Consequences

- Operators and agents can place a Coinbase-spot long with SL/TP without authoring a strategy.
- Discretionary and strategy deployments share intent persistence, unique client ids, the risk
  registry, paper matching, live OCO, and reconcile-before-retry.
- Intra-strategy pyramiding, shorting, attached entry brackets, daily-loss breakers, `1m`/`2h`/`4h`
  execution clocks, extra exchanges, and YOLO-without-confirm for live stayed out of this slice.
  Later [ADR 0040](0040-venue-strategy-paper-live-htf-clocks.md) widened clocks,
  [ADR 0043](0043-yolo-live-skip-confirm.md) added live skip-confirm, and
  [ADR 0045](0045-spot-shorting-and-attached-entry-brackets.md) shipped shorting and attached
  entry brackets. Pyramiding and extra exchanges remain destination.

## Alternatives considered

- **Bypass the broker from the UI/CLI:** rejected; ADR 0031 requires an order intent and risk.
- **Require a published dummy strategy:** rejected; that would pollute research fingerprints.
- **Treat timeouts as rejects and resubmit:** rejected; unique client ids plus GET-order
  reconcile are the ambiguous-timeout contract.
- **Allow discretionary when allocations are nonempty:** rejected for this slice; allocations are
  a strategy allowlist and fail closed.
- **Attached `attached_order_configuration` on the entry:** deferred; same as Phase 13 — rest the
  entry first, then one OCO after fill so unfilled entries do not rest exits.
