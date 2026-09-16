# 0045: Spot shorting and attached entry brackets

- Status: Accepted
- Date: 2026-09-16
- Relates to: [0004](0004-safe-execution-and-access.md), [0005](0005-canonical-strategy-schema.md),
  [0009](0009-deterministic-bar-level-backtest-engine.md),
  [0019](0019-ops-contract-identity.md), [0031](0031-coinbase-first-platform-end-state.md),
  [0033](0033-phase-10-risk-policy-registry.md), [0036](0036-phase-13-live-extras.md),
  [0039](0039-on-demand-discretionary-trades.md),
  [0043](0043-yolo-live-skip-confirm.md),
  [0044](0044-parameter-sweeps-wfo-stitched-equity.md)

## Context

On-demand longs with required SL/TP already persist an intent, pass the risk registry, and
reconcile ambiguous timeouts ([ADR 0039](0039-on-demand-discretionary-trades.md)). Live still rests
`trigger_bracket_gtc` **after** an entry fill ([ADR 0036](0036-phase-13-live-extras.md)). Strategy
documents still require `entry.side: "long"`. Destination product includes shorting and attached
entry brackets ([ADR 0031](0031-coinbase-first-platform-end-state.md)).

Coinbase Advanced Trade **spot** accepts `BUY`/`SELL` and
`attached_order_configuration.trigger_bracket_gtc` (size omitted; the child inherits the parent
fill). It does not offer a borrow-to-short on that spot create-order path. Futures, International
perps, `leverage`, and `margin_type` stay out.

Live YOLO skip-confirm ([ADR 0043](0043-yolo-live-skip-confirm.md)) and research WFO
([ADR 0044](0044-parameter-sweeps-wfo-stitched-equity.md)) are separate slices and are not
rewritten here.

## Decision

Ship spot-capable shorting and attached entry brackets through the existing order-intent → risk →
broker path. Keep `schema_version: "1.0"`. Existing `"side": "long"` documents stay byte-identical.

### Shorting (spot, no silent futures)

- Strategy `entry.side` is `"long"` or `"short"`. Discretionary HTTP/CLI accept `side` with default
  `"long"`.
- Geometry: longs require `stop < entry < take_profit`; shorts require
  `take_profit < entry < stop`. Trailing ratchets **down** for shorts (lowest low; stop never
  increases).
- Paper and backtest V1/V2/V3 simulate a cash-and-inventory spot short: sell-to-open credits quote,
  buy-to-cover spends quote, Decimal accounting, no leverage multiple, no borrow interest. Long
  golden fingerprints stay unchanged.
- Live submits Coinbase Advanced Trade **SPOT** `SELL` for short entries. Fail closed when available
  base is missing or below the quantized quantity (`INSUFFICIENT_BASE_FOR_SPOT_SHORT`). Never set
  `leverage` or `margin_type`, never change `product_type` away from `SPOT`, never route to
  derivatives.
- Risk marks **absolute** quote notional of open positions and working entries (BUY to open a long
  or SELL to open a short). Allocations nonempty still deny discretionary.
- Intra-strategy pyramiding, extra exchanges, and live place-order YOLO stay out. `--i-understand-live`
  remains required for live start and live place-order.

### Attached entry brackets

When stop and take-profit are known at entry persist time **and** ATR trailing is disabled
(discretionary always; strategy when trailing is `{"enabled": false}`):

- Persist those prices on the **entry** intent (`stop_trigger_price` and `take_profit_price`).
- Live create-order includes top-level `attached_order_configuration.trigger_bracket_gtc` with
  `limit_price` = take-profit and `stop_trigger_price` = stop, **omitting size**.
- After an attached entry fill, do **not** submit a second post-fill `trigger_bracket_gtc`.
- Paper still never sends venue brackets or attached configuration. Synthetic SL/TP arm after fill
  from the prices already on the entry intent.

When ATR trailing is enabled, live keeps the ADR 0036 post-fill OCO so cancel/replace of a recorded
child stays restart-safe. Time-exits still cancel working exits and submit a marketable cover.

Timeouts stay `UNKNOWN`; GET-order reconcile; never retry create-order for that client id.

### Persistence and ops contract

Alembic `0027` adds nullable `order_intents.take_profit_price` and
`execution_orders.take_profit_price`, and `execution_positions.side` (`long` default, CHECK
`long`/`short`). Ops contract becomes `thytrader-ops-contract-v15` with
`position_sides` `long`/`short`, `attached_entry_brackets` `paper`/`live`, and
`expected_schema_revision` `0027`.

## Consequences

- Operators and agents can place a paper short or a live spot sell-to-open with SL/TP attached to
  the entry. Live shorts that lack base inventory fail closed instead of borrowing.
- Backtest, paper, and live share published `entry.side` and the same stop/target geometry.
- Unfilled live entries no longer rest independent exits when the bracket is attached.
- Extra exchanges, futures, live place-order YOLO, WFO/research studies, and intra-strategy
  pyramiding stay out of this slice.

## Alternatives considered

- **Silent Coinbase futures / margin create-order:** rejected; this slice is spot-capable only.
- **Attached brackets plus trailing replace without a recorded child:** rejected; fail closed rather
  than rest a second OCO.
- **Bump `schema_version` to 1.1:** rejected; `"short"` is an added enum member and omitted
  attached prices preserve existing long documents.
- **New backtest engine id for shorts:** rejected; long V1/V2/V3 bytes stay identical when
  `entry.side` is `long`.
