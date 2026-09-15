# 0036: Phase 13 live extras — 5m live, trailing stops, user-order WS, native OCO

- Status: Accepted
- Date: 2026-09-15
- Relates to: [0004](0004-safe-execution-and-access.md), [0005](0005-canonical-strategy-schema.md),
  [0018](0018-5m-paper-not-live.md), [0019](0019-ops-contract-identity.md),
  [0030](0030-agent-e2e-primary-surface.md), [0031](0031-coinbase-first-platform-end-state.md)

## Context

Paper already evaluates the same published `5m` clock as research. Live still rejected anything
but `1h` ([ADR 0018](0018-5m-paper-not-live.md)) because user-order WebSockets, native stop/OCO,
and durable trailing were missing. Phase 13 is those live extras. Daily-loss / drawdown breakers
remain destination ([ADR 0031](0031-coinbase-first-platform-end-state.md)); Phase 10 already
shipped the risk-policy registry.

On-demand discretionary trades with SL/TP are **not** this slice. They need a later ADR for
intent origin, risk, and venue vs synthetic exits.

## Decision

Ship one vertical live-extras increment:

### 5m live

`create_deployment` accepts published `1h` or `5m` for **paper and live**. HTF-filter publications
stay rejected. `15m` / `30m` / `6h` / `1d` remain dataset (and research HTF) clocks only. `1m` /
`2h` stay destination.

Live 5m uses the same closed-bar worker as paper: `new_closed_bars` steps by
`interval.duration`, pauses when the latest bar is missing or gapped, and replays contiguous
missed bars after downtime.

This **supersedes** ADR 0018's live-1h-only gate. Paper 5m semantics are unchanged.

### Native OCO / brackets

After a live entry fill, the runtime persists one SELL intent then submits Coinbase Advanced
Trade `trigger_bracket_gtc` with `limit_price` = take-profit and `stop_trigger_price` = stop.
That single venue order is the OCO: filling either leg cancels the other. Paginated REST fills
remain the fill ledger. REST GET-order after submit is unchanged.

Paper keeps the existing OCO **simulation**: rest a post-only take-profit and fire a marketable
stop when the closed bar trades through the stop, canceling the resting TP first. It does not
send `trigger_bracket_gtc`.

While a live bracket is open, the worker must **not** also submit a synthetic marketable stop
from `candle.low`. Time-exits still cancel the bracket and submit a marketable sell. If a live
position is OPEN without a working bracket, the worker places one or pauses.

### User-order WebSockets

The execution worker supervises an authenticated Coinbase `user` channel (JWT, heartbeat,
reconnect backoff) whenever live credentials exist. Durable singleton state records connection
lifecycle without secrets. Operator `runtime` reports that state.

User-channel frames are observation. They may trigger earlier REST reconcile for a matching
`client_order_id`. They are not the fill ledger.

**5m live** pauses when the user-order feed is not `connected` (including stale heartbeat).
**1h live** still reconciles through REST each cycle; a down user feed is reported but does not
by itself pause 1h.

### Trailing stops

The schema keeps `{"enabled": false}` as the only legal disabled form so existing fingerprints
stay stable. Enabled trailing is:

```json
{
  "enabled": true,
  "kind": "atr_multiple",
  "atr_indicator": "atr",
  "multiple": "1.5"
}
```

`atr_indicator` must name an LTF ATR. `multiple` is a plain decimal in `[0.5, 10]`, the same
bounds as the initial ATR stop.

Long-only ratchet: persist `trail_extreme` (highest high since the fill bar). Each later closed
bar, `candidate = trail_extreme − ATR × multiple`, quantized to the product increment. The
working stop is `max(current_stop, candidate)` and never decreases. The fill bar uses only the
initial stop. Backtest V1/V2/V3, paper, and live share this arithmetic. Disabled trailing is a
no-op, so existing golden results stay byte-identical.

Live trailing that raises the stop cancels the resting bracket and replaces it. Paper updates
`position.stop_price` only. Trailing requires a defined ATR on the bar; if ATR is missing the
stop is left unchanged (not zero).

### Persistence and ops contract

Alembic `0022` is forward-safe: nullable `execution_positions.trail_extreme`, nullable
`order_intents.stop_trigger_price` and `execution_orders.stop_trigger_price`, widen
`ck_order_intents_kind` with `trigger_bracket`, and singleton `user_order_feed_state`.

Ops contract becomes `thytrader-ops-contract-v8` with `live_timeframes` `1h` and `5m` and
`expected_schema_revision` `0021` → `0022`.

## Consequences

- A published 5m strategy can arm live when credentials exist; HTF-filter fingerprints still 409.
- Live SL/TP after fill is a venue OCO, not two uncoordinated resting orders.
- Synthetic trailing is durable and restart-safe; native Coinbase trailing is not used.
- Agent CLIs fail closed on a v7 image (`live_timeframes` mismatch) until `make run`.
- Daily-loss kill, on-demand SL/TP, paper/live HTF, `1m`/`2h` clocks, journals, and notify stay
  out of this slice.

## Alternatives considered

- **Arm 5m live without user WS / native OCO:** rejected; that is the ADR 0018 microstructure gap.
- **Require a healthy user feed to keep 1h live running:** rejected for this slice; 1h already
  reconciles via REST. 5m intra-bar fills need the WS liveness gate.
- **Attached entry brackets (`attached_order_configuration`):** deferred; entry remains a
  post-only buy, then a separate OCO after fill so unfilled entries do not rest exits.
- **Percentage trailing / native venue trailing:** deferred; ATR-multiple matches the shipped
  initial stop kind.
- **Treat user WS as the fill ledger:** rejected; timeouts stay ambiguous and REST fills remain
  authoritative.
