# 0148: Live agent desks

- Status: Accepted
- Date: 2026-10-22
- Relates to: [0147](0147-agent-desk.md) (agent desks), [0039](0039-on-demand-discretionary-trades.md)
  (discretionary books), [0124](0124-inventory-adoption.md) (unclaimed base)

## Context

Agent desks (ADR 0147) were paper only. An operator who has watched a desk's journal can want
its model to trade real money inside a small, explicit budget. The risk policy already decides
who may spend live capital: while any `allocations` are in force, live entries need a listed
strategy and every discretionary order is denied (`DISCRETIONARY_NOT_ALLOCATED`), so unreserved
orders cannot spend capital other bots have reserved. A desk has no strategy id.

A live discretionary book's `cash` is the venue balance it shares with every other live book,
not the desk's allocation, so the paper settlement rule (return the book's cash) cannot carry
over.

## Decision

1. **A desk has a mode.** `mode: live` is created only with `i_understand_live` (HTTP 428
   `live_acknowledgement_required` otherwise; CLI `--mode live --i-understand-live`, which YOLO
   never covers). The acknowledgement time is stored on the desk. Paper stays the default.
2. **The policy reserves a live desk's capital.** `RiskPolicyDefinition.desk_allocations` lists
   `{desk_id, allocated_quote}` in the policy's quote currency (CLI `set-risk-policy
   --desk-allocation DESK_ID:QUOTE`). It is omitted from canonical bytes when empty, so existing
   policies keep their fingerprints, and it counts toward the same total as `allocations`. While
   any strategy or desk allocation is in force, live discretionary orders are an allowlist: only
   a listed desk's orders are members. Desk allocations never gate strategy books. The
   reservation is cumulative: the allocations the desk's open books hold plus the whole new
   allocation (fee headroom included) must fit (`ALLOCATION_EXCEEDED`); a product quoted in another currency is
   refused.
3. **Live desk orders use the live discretionary path unchanged**: the live broker, the venue's
   remaining quote and unclaimed base, the shared-collateral scope, the exposure caps, the
   breakers, and venue-attached stop and target. A live spot desk is long only
   (`DESK_SHORT_LIVE_UNSUPPORTED`) and enters with post-only limits only (`DESK_LIVE_LIMIT_ONLY`):
   a market order is sized at a candle close but can fill higher, while a limit never fills
   above its price, so an order's spend stays inside its allocation.
4. **Books are valued by their own fills in both modes**: allocation, plus sale proceeds, minus
   purchase cost, minus fees, plus held quantity at the mark. Settlement returns that value when
   the book is flat and stopped, every recorded fill is applied and matches the position, and,
   live, every cancelled order has passed its settlement watermark (ADR 0141), after which a
   late fill is not expected. Book `cash` is never read, so a shared venue balance cannot leak
   into a pool. Held books are marked at the newer of the hourly close and their own clock's current
   close (by when the bar ended); if neither is current, equity is unknown and entries stop.
5. **The model is told.** A live desk's prompt says every order spends real money and that it
   may open long only. Fees are described as they are charged: limit entries at maker, market
   entries, stops and targets at taker.
6. **One answer, one order; exact recovery.** A desk order's idempotency key is the briefing
   fingerprint, and before reserving cash the desk asks execution whether an order already exists
   under that key (`DESK_DUPLICATE_ANSWER`), so a retried or replayed answer cannot buy twice. The reservation
   chooses the new book's id before placing; recovery links exactly that book (in the desk's
   mode, on its product) or refunds, and a store outage never reads as "no book".

## Consequences

- Ops contract v108 advertises `agent_desk_modes` `["paper", "live"]`.
- A live desk can hold at most its policy reservation across its open books, and at most its
  pool; the account's own balance and the policy's account caps still apply.
- A desk cannot be switched between modes; create a new desk.
- Promotion is the operator's call. ThyTrader does not move a paper desk live.

## Amendment (2026-10-28): changing a live desk

`PATCH /api/v1/desks/{id}` (`thytrader-desk update`, ADR 0147 amendment) on a live desk needs
`i_understand_live` (HTTP 428 otherwise) to change who trades (harness, model), what it is told
(mandate) or its caps: each changes how real money is traded, so it takes the same
acknowledgement as creating the desk. Its name and cadence change without it. The mode cannot
change. A failing or stalled live desk raises a critical `DESK_WAKES_FAILING` /
`DESK_WAKES_STALLED` alert, delivered like any critical alert.
