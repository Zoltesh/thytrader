# 0134: Live Coinbase CFM futures order path

- Status: Proposed; P2-4 persistence amended by [0135](0135-durable-futures-safety-obligations.md)
- Date: 2026-10-10
- Relates to: [0106](0106-account-risk-capital-and-live-startup-baselines.md),
  [0107](0107-capital-normalized-live-performance.md),
  [0117](0117-truthful-inventory-and-fleet-controls.md),
  [0126](0126-futures-instrument-catalog-read-only.md),
  [0127](0127-cfm-futures-account-mirror.md),
  [0128](0128-futures-backtest-model.md),
  [0129](0129-paper-futures-books-and-shared-collateral-risk.md),
  [0130](0130-fleet-entry-health.md),
  [0132](0132-execution-cycle-shared-reads-and-budget.md),
  [0133](0133-cfm-supervised-trade-evidence-and-fixed-contract-fee.md)

## Context

Coinbase CFM futures are read-only in ThyTrader: catalog, funding history and candles
(ADR 0126), the account mirror (ADR 0127), backtests (ADR 0128) and paper books on the shared
USDC collateral pool (ADR 0129). ADR 0133 measured what the venue bills. This ADR designs the
live order path for perp-style contracts, in slices that stay unreachable until the last one
turns live futures starts on.

### What the code does today

1. **Broker.** The Coinbase spot broker is one adapters-layer module near its 800-line budget
   and the only member of the `venue_adapters` component in `tests/package_layers.json`. Its
   spot assumptions are explicit: `place_order` refuses non-spot ids, the client-id lookup
   and fills query filter `product_type: SPOT`, and the fill parser rejects non-spot fills.
   New Coinbase order modules that import `trading` or `execution` join that component (a
   component mapping, not the upward-import allowlist).
2. **One live broker per venue generation.** The execution worker builds one `live_broker`
   per venue; the API builds its own spot broker for discretionary orders.
3. **Order invariants already hold for every live order** through `submit_intent`: the intent
   is persisted before the POST, client ids are `deployment:purpose:stamp:uuid7`, and
   `BrokerError`, `ValueError` and `TimeoutError` become UNKNOWN. Reconciliation recovers
   UNKNOWN orders by client id and ingests fills order by order. A refusal raised as
   `BrokerError` *before* anything is sent also becomes UNKNOWN and pauses the book as an
   unconfirmed submit, so the futures broker returns `SubmitResult(REJECTED, reject_reason=…)`
   for pre-request refusals.
4. **Paper futures books are unit-consistent.** Domain quantities are base-equivalent
   (contracts x `contract_size`). The paper book preparer returns an unbound state for live,
   and the futures risk entry points refuse live (`FUTURES_LIVE_UNSUPPORTED`; futures capital
   is 0 for live).
5. **Start refuses live futures in three places:** the HTTP start route (409
   `FUTURES_LIVE_UNSUPPORTED`), the runtime CLI start handler and the futures start service.
6. **The live entry path is spot-shaped.** It refuses live shorts without base
   (`INSUFFICIENT_BASE_FOR_SPOT_SHORT`), stamps the spot balance of the strategy's quote
   currency, sizes fees from the spot profile, and the futures margin verdict reads ledger
   equity, which starts at 0 for a live book (ADR 0106), so every live futures entry would be
   denied.
7. **Exits are generic and cancel first.** Every protective, signal, time, liquidation and
   flatten exit cancels and confirms resting orders, then sends one `OrderKind.MARKETABLE`
   order. Live protection rests `TRIGGER_BRACKET` or `STOP_LIMIT` orders. `stop --flatten` and
   fleet-flatten defer when no verified candle exists.
8. **Collateral.** Any CFM position marks the shared pool `in_use`, which denies live
   USD/USDC spot entries unless the declared L3 reserve covers initial margin x haircut
   (ADR 0129). Linked breakers and beta netting apply to paper books only.
9. **The policy allowlist cannot hold futures ids:** `product_allowlist` validates against the
   spot id pattern, so a non-empty live allowlist would deny every futures start with
   `PRODUCT_NOT_ALLOWLISTED`.
10. **Supporting surfaces.** The user websocket feed only nudges reconciliation by client id.
    Alert `code` and `scope` columns are unconstrained, so new codes need no migration. Fleet
    health has a `CFM-USD` scope but does not yet run a futures envelope (ADR 0130 gap).

### What the venue does

From the public API reference and a GET-only read of a supervised one-contract round trip
(ADR 0133), now pinned as synthetic fixtures in `tests/exchanges/fixtures/coinbase_futures/`:

- Futures orders report `product_type: FUTURE`; a market order reports `order_type: MARKET`,
  `time_in_force: IMMEDIATE_OR_CANCEL`, echoes `client_order_id`, and carries an
  `is_liquidation` flag.
- Order `filled_size`, `base_size` and fill `size` are **contracts**, not base units.
  `size_in_quote` is false.
- Fill `commission` and order `total_fees` are all-in (rate part plus the fixed per-contract
  part) and equal the itemized `commission_detail_total` lines exactly; its
  `total_commission` is rounded to cents and is not the fee.
- Fills carry `trade_type: FILL`, empty `future_legs` and `option_legs`, and an empty
  `realized_pl`. `future_legs` is "empty for non-combo fills", so a non-empty one is a combo.
- A flat account's `cfm/positions` is `{"positions": []}`.
- **Reduce-only exists on market orders.** Historical futures orders carry
  `order_configuration.market_market_ioc.reduce_only` (a boolean; `false` on an app-placed
  close). The Coinbase app closed that position with a plain market IOC order, not with
  `close_position`. The create-order reference documents no `reduce_only` request field, but
  lists `REDUCE_ONLY_NOT_ALLOWED_ON_VENUE`, `REDUCE_ONLY_INCREASED_POSITION_SIZE` and
  `REDUCE_ONLY_NOT_ALLOWED_ON_SPOT_PRODUCTS` failure reasons.
- `POST /orders/close_position` takes `client_order_id`, `product_id` and an optional `size`
  ("the amount of contracts that should be closed") and returns the create-order response
  shape; `CANNOT_CLOSE_ZERO_POSITION` is a documented failure reason (the venue answered
  HTTP 404 `NOT_FOUND` instead, §1). The reference says nothing about partial sizes, resting
  holds or flips.
- Create order lists `INVALID_FCM_TRADING_SESSION` and `FUTURES_AFTER_HOUR_INVALID_ORDER_TYPE`
  / `_TIME_IN_FORCE`; only `TriggerBracketGtc` is eligible as an attached order.
- Funding accrues hourly and settles twice daily (venue launch material); ADR 0133 observed
  that `funding_pnl` is not booked hourly.

## Decision

### 1. How positions are reduced

**Planned default: every live futures `MARKETABLE` order is sent as
`POST /orders/close_position` with `size` = the book's contracts.** Futures entries and adds
are post-only limits only; the futures broker sends no market-entry body.

- It is the one venue path documented as closing a position, so even a misrouted call can only
  reduce exposure, and a flat account gets a definite rejection.
- Every exit already funnels through one marketable-exit function, so no new `OrderKind`, CHECK
  migration or call-site review is needed.
- CFM nets per product across the account and `close_position` acts on the account's
  position, so it is correct only under product exclusivity (I3).

**Supervised probe (planned before any live wiring):** a `market_market_ioc`
order with `reduce_only: true` and `base_size` = the book's contracts. The probe answers:

1. Is `reduce_only: true` accepted in the create-order body for a CDE perp?
2. Sent while flat or on the wrong side, is it a definite rejection
   (`REDUCE_ONLY_INCREASED_POSITION_SIZE` or similar 4xx), never an opening order?
3. Does a reduce-only size larger than the position reduce to flat, or reject?
4. Does it echo `client_order_id` and report `reduce_only: true` in the historical order?

And for `close_position`: a partial `size`; the error shape when flat; whether resting stops
(holds) block it; whether it can ever flip the position.

**Answered 2026-10-16 (supervised probe, one ETP contract):** CDE does **not** allow
reduce-only. As a top-level body field `reduce_only` is an unknown proto field (HTTP 400);
inside `market_market_ioc` it is recognized but `orders/preview` refuses it with
`PREVIEW_REDUCE_ONLY_NOT_ALLOWED_ON_VENUE`. `close_position` of one contract filled at once and
echoed a `market_market_ioc` configuration. The `REDUCE_ONLY_MARKET` candidate is therefore
removed from the futures broker: every marketable reduction is `close_position`.

**`close_position` answered 2026-10-16 (second supervised probe, at most two ETP contracts):**

- A partial `size` reduces by exactly that many contracts (two open, `size` 1, one left).
- Without a matching position it places nothing and answers HTTP 404 `NOT_FOUND` ("no positions
  found for product"), both when flat and when `size` exceeds the open contracts (one long,
  `size` 2). It never reduced to flat or flipped. The broker maps this to a definite rejection
  (`coinbase_http_404:NOT_FOUND`). The same 404 for "flat" and "oversized" means the rejection
  says nothing about the venue's position: the reduction path already submits only after fresh
  venue contracts equal the book's (`_reduction_proven`).
- A resting stop-limit on the same product does **not** block it (no hold), and the stop stays
  OPEN after the position is closed. Cancel-first (decision 7) and the I9 orphan cancel are
  therefore required, not belt and braces: a stop left behind would open a position.

A plain
market order **without** venue-enforced reduce-only is never used to reduce a position: sized
to the book while the account is already flat, it would open a position.

### 2. Invariants and refusal codes

| # | Invariant | Code(s) |
|---|---|---|
| I1 | A live futures start needs `i_understand_live` **and** `i_understand_live_futures`. YOLO never covers it (a hard gate, like `--adopt-holdings`). | HTTP 428 `LIVE_FUTURES_ACK_REQUIRED` |
| I2 | Live futures need a policy opt-in: `futures.live_enabled: true`, `futures.live_capital_usd` and `futures.product_allowlist` (futures id pattern). It gates starts and entries, never exits. | `FUTURES_LIVE_DISABLED`, `FUTURES_LIVE_CAPITAL_UNSET`, `PRODUCT_NOT_ALLOWLISTED` |
| I3 | **Product exclusivity.** At most one running live futures book per product, and no external CFM position or nonterminal external futures order on that product at start or entry. | `FUTURES_PRODUCT_OCCUPIED`, `FUTURES_EXTERNAL_POSITION_ON_PRODUCT` |
| I4 | Perp-style contracts only; dated contracts are refused until an expiry flatten exists. | `FUTURES_LIVE_DATED_UNSUPPORTED` |
| I5 | Each order is 1 to `max_order_contracts` whole contracts. Unset means **1** for live, and the position cap equals it (futures forbid pyramiding). | `FUTURES_ORDER_CONTRACTS_EXCEEDED` (exists); broker `FUTURES_FRACTIONAL_CONTRACTS` |
| I6 | The broker converts base-equivalent quantity to contracts and back exactly. An unknown contract size, a fractional count, a non-futures id, a spot fill shape, `size_in_quote: true` or a combo fill (non-empty `future_legs`) is refused or fails closed. | broker REJECTED `FUTURES_CONTRACT_SIZE_UNKNOWN`; `BrokerError` on fills |
| I7 | A futures marketable order can only be a position reduction (§1). Entries are post-only limits. | broker REJECTED `FUTURES_ORDER_KIND_UNSUPPORTED` |
| I8 | Admission needs fresh venue evidence (≤ 180 s, in-cycle read or mirror): `futures_buying_power` ≥ proposed initial margin, the L3 reserve covering (current + proposed initial margin) x haircut, the venue killswitch off, and no persisted maintenance window. | `FUTURES_COLLATERAL_UNKNOWN` (exists), `FUTURES_BUYING_POWER_SHORT`, `FUTURES_COLLATERAL_RESERVE_SHORT` (projected), `FUTURES_VENUE_KILLSWITCH`, `FUTURES_VENUE_MAINTENANCE` |
| I9 | The venue position equals the book position, in contracts and side, whenever the comparison is valid (no nonterminal order, no unapplied fill, venue read newer than the last applied fill + 5 s). Two consecutive valid mismatches pause the book. | `FUTURES_POSITION_MISMATCH` (pause) |
| I10 | The live margin monitor is protective, never gated, and runs under fleet disarm, managed stop and `live_enabled: false`. | `IntentPurpose.LIQUIDATION` (exists) |
| I11 | The bound contract size equals the catalog contract size, otherwise entries are denied; exits still run at the catalog size the venue uses. | `FUTURES_CONTRACT_DRIFT` |
| I12 | USD futures figures are never added to USDC. The only cross-currency step is ADR 0129's threshold check under a declared peg. | (never-summed property test extended to live) |

All new risk reason codes leave `pauses_risk_increasing` false; only I9 pauses, and it pauses
the book.

### 3. Units, loss, exposure and collateral

- **Units.** Orders, fills, positions, exposure and the ledger stay base-equivalent `Decimal`.
  Contracts exist only at the broker boundary, in audit events and in reports (quantity /
  bound `contract_size`). No schema change.
- **Daily loss.** The `CFM-USD` scope covers book equity change (fills, fees, accrued funding)
  since the UTC day open, against the daily-loss fraction of `live_capital_usd` and
  `max_daily_loss_usd`. Linked breakers go live in both directions; nothing is summed (I12).
  The venue `daily_realized_pnl` is shown beside it for cross-checking only (it is gross of
  fees, ADR 0133).
- **Exposure.** Futures-scope gross notional and the futures beta cap are measured against
  `live_capital_usd`. Spot caps are unchanged; a futures short never lowers them until hedge
  mode's opt-in live netting. Futures entries count toward the ADR 0125 clustering window.
- **Live book equity** = `allocated_capital` + ledger equity (paper is unchanged), so the
  margin verdict no longer starts from 0.
- **Shared collateral.** Managed positions make the pool `in_use`, so live spot entries rely on
  the L3 reserve. A rise in the underlying raises initial margin and can pause *all* live spot
  entries with `FUTURES_COLLATERAL_RESERVE_SHORT`; fleet health and its alert show it. The
  operator sizes the reserve with headroom for that.

### 4. Kill switches

| Control | Futures entries | Protection and margin monitor | Effect on the position |
|---|---|---|---|
| fleet-disarm | blocked | continue | none |
| `futures.live_enabled: false` | blocked (futures only) | continue | none |
| managed stop | blocked | protection rests at the venue; margin monitor continues | none |
| `stop --flatten` / fleet-flatten | blocked | protection cancelled (confirmed) | position reduced to flat (§1), then I9 confirms the venue is flat |

### 5. Slices

Each slice is mergeable alone and stays unreachable until P2-7. The pilot needs P2-0 through
P2-7 in order.

| Slice | Layer | Delivers | How the operator notices |
|---|---|---|---|
| P2-0 | docs, tests | This ADR and synthetic fixtures of futures orders, fills, flat positions, a combo fill, a quote-sized fill, `close_position` success and failure, and an FCM-session rejection, with a test pinning the facts below. | Not applicable (no runtime change). |
| P2-1 | adapters | Move-only split of the Coinbase broker: fills parsing and order-JSON helpers into their own modules, added to `venue_adapters`; AST-identical bodies, byte-identical OpenAPI, schema and CLI help. | Not applicable (no behavior change). |
| P2-2 | adapters, execution | `CoinbaseFuturesBroker` (post-only `limit_limit_gtc` entries in contracts, `STOP_LIMIT`/`TRIGGER_BRACKET` protection, marketable orders as the §1 reduction, FUTURE fills and client-id lookup, contract conversion, pre-request REJECTED results); a futures fill parser enforcing I6; a `ContractSizeSource` protocol; `ProductRoutedBroker` routing by product id (`FUTURES_BROKER_UNAVAILABLE` without a futures broker). A pin that the module's POST paths are only create, the chosen reduction path and batch cancel. | Nothing is reachable yet. Later, a pre-request REJECTED takes the existing rejection-latch pause and raises `BOOK_PAUSED_MISMATCH`. |
| P2-3 | risk | Live futures policy fields (excluded while unset, so fingerprints do not change), a live futures deployment and entry gate (I2–I5, I8, I11), live futures capital, futures allowlist at deployment, live linked breakers, live book equity. | Every denial in the decision and why-trade journals; `risk` shows the new fields; fleet health runs the gate for `CFM-USD` (P2-6). |
| P2-4 | execution, processes | Venue wiring (futures broker, CFM reader, contract-size source); live futures book preparation with one shared venue read per cycle (two GETs, only while a live futures book runs); hourly funding accrual; the I9 check with the flip guard (§6a) and orphan-protection cancel; the live margin monitor (I10); futures-aware entry, sizing and fee paths; flatten without a candle (§6b). Durable obligations use Alembic 0076 (ADR 0135). | A paused book raises `BOOK_PAUSED_MISMATCH` naming the contracts; de-risking shows as a LIQUIDATION trade reason plus the P2-6 alert; overdue funding blocks entries with `FUNDING_HISTORY_MISSING`, shown in `futures-books` and fleet health. |
| P2-5 | execution | Venue protection for futures, decided by the supervised probe: if CDE accepts stop-limit and trigger-bracket orders, nothing beyond P2-2; otherwise a worker-side synthetic stop that sends a STOP-purpose reduction, needing healthy market data and a held lease. **Decided 2026-10-16:** CDE accepts both (§7 item 4), so protection is venue-held through the P2-2 request shapes; no synthetic stop. | The existing `STOP_UNCOVERED`, `STOP_COVERAGE_UNKNOWN` and `STOP_TRIGGERED_UNFILLED` alerts, tested for futures books. |
| P2-6 | services, coordination, web | New alerts (`FUTURES_POSITION_MISMATCH`, `FUTURES_ACCOUNT_UNKNOWN`, `FUTURES_MARGIN_LOW`, `FUTURES_FUNDING_DRIFT`, `FUTURES_EXTERNAL_ON_MANAGED_PRODUCT`, `FUTURES_ORPHAN_ORDER`) from a per-cycle futures supervisor that never fails the cycle; live columns in `futures-books`; managed/mismatch/external rows in venue reconciliation; readiness findings; a `futures_live` health component; the live futures gate in fleet health (closes the ADR 0130 gap); web venue facts; skills, schema and ops contract. Mandatory before switch-on. | This slice is the answer for every check in P2-3 and P2-4: each is in a report, an alert delivered through the notify provider, or the Home banner. |
| P2-7 | interfaces, API, execution | The live start path: `fee_per_contract` and `futures_capital_usd` required (pinned as performance capital, ADR 0107), perp-only, exclusivity and venue evidence at start; HTTP 428 `LIVE_FUTURES_ACK_REQUIRED` replaces the 409; CLI `--i-understand-live-futures` and `--futures-capital-usd`; `FUTURES_ORDER_PATHS` in the ops contract; runtime skill (flags, stop/flatten semantics, futures-only disarm, I9 repair runbook); AGENTS.md known gaps. | An audited `deployment_started` event with `futures: true`, and `futures-books` listing the live book. |

**After the pilot:** P2-8 hedge mode (§6e), P2-9 import of venue-originated fills (liquidation
or ADL, detectable through `is_liquidation`) as the I9 repair, P2-10 funding true-up (§6d),
P2-11 dated contracts with an expiry flatten, P2-12 websocket futures balance and position
evidence.

### P2-6 implementation status

The initial read-only safety observer and `health.futures_live` consumer are implemented.
The observer covers account blindness, configured margin boundaries and pending de-risk,
persisted I9 mismatch pauses, and external-order facts from an explicitly injected source.
It reuses durable alert dedupe/recovery/delivery and existing account evidence. It does not
construct a production futures broker or authorize any mutation.

P2-6 adds persisted live-book visibility, strict managed/mismatch/external reconciliation,
readiness duties and collateral headroom, the actual P2-3 admission probes for live CFM fleet
health, and Home/bot venue facts. Confirmed orphan-cancellation audit events produce one-off
warnings through the existing durable feed, not another cancellation. The bounded newest-500
audit window is explicit, not an assertion of complete historical event coverage.

The remaining external evidence boundary is **funding settlement provenance**. No available
mirror field proves settlement boundaries or fully managed interval coverage. Therefore
`FUTURES_FUNDING_DRIFT` is reserved but unobserved, and every live book explicitly exposes
unknown with `SETTLEMENT_PROVENANCE_UNAVAILABLE`. Neither a `funding_pnl` delta nor an hourly
modeled accrual is manufactured into a settlement. This boundary must be resolved with
validated venue evidence before a funding-drift producer can be claimed. All live start
surfaces retain `FUTURES_LIVE_UNSUPPORTED`; `FUTURES_ORDER_PATHS` stays empty and every
capability remains `orderable: false` pending authentic acceptance and P2-7.

### 6. Decisions taken

- **a. Flip guard.** When I9 finds the venue position larger than the book or on the opposite
  side, and the excess is proven ours (fills of an order carrying a managed client id), the
  worker reduces the excess (risk-reducing, §1) and then pauses the book with
  `FUTURES_POSITION_MISMATCH`. An excess not proven ours is never touched: the book pauses and
  `FUTURES_EXTERNAL_ON_MANAGED_PRODUCT` is raised.
- **b. Flatten without a candle.** Live futures `stop --flatten` and fleet-flatten proceed
  without a verified candle, because the reduction needs no price; the latest mark is used for
  the journal only.
- **c. Pilot values are operator policy, not design.** `futures.live_enabled`,
  `live_capital_usd`, `product_allowlist`, `max_order_contracts` (live default 1),
  `live_derisk_margin_ratio`, `live_funding_drift_tolerance_usd`, the L3 reserve and the
  concurrent-deployment limit are policy fields the operator sets and publishes. The skills
  explain how to size them; no specific values are part of this decision.
- **d. Funding.** In P2, live books accrue funding hourly at the settled rate (the paper
  model) and `FUTURES_FUNDING_DRIFT` compares the accrual with the venue's `funding_pnl`
  change at each settlement. A true-up to venue settlements (P2-10) follows once the
  settlement cadence and `funding_pnl` semantics are measured.
- **e. Hedge mode** is P2-8, recorded by a separate ADR: a separate deployment kind with no
  stop on the hedge leg (a stop is anti-hedge), protected by the margin monitor and I9; the
  target short is derived from linked managed spot base; increases are gated entries and
  decreases use the §1 reduction; live netting only for managed books that pass I9 with
  current funding and fresh venue evidence.
- **f. Start surfaces.** P2 starts live futures through the CLI and HTTP API only. The web
  start form stays paper-only for futures.

### 7. Supervised probes (one contract, run before the slice that depends on them)

1. Order and fill units, commission composition, `product_type`, `order_type`, client-id echo:
   **answered** by P2-0 (contracts; all-in commission; FUTURE; MARKET/IOC; echoed).
2. Reduce-only market IOC vs `close_position` (§1): **answered** 2026-10-16: reduce-only is
   not allowed on CDE; `close_position` closes, takes a partial `size`, answers 404 `NOT_FOUND`
   when flat or oversized (never flips), and is not blocked by a resting stop (§1).
3. Post-only `limit_limit_gtc` accepted on FCM: **answered** 2026-10-16: accepted on a 24/7
   perp (rested OPEN, cancelled).
4. `stop_limit_stop_limit_gtc`, standalone `trigger_bracket_gtc` and attached brackets on a
   futures entry: **answered** 2026-10-16: all accepted. A standalone bracket needs an open
   position (`PREVIEW_BRACKET_ORDER_SIZE_EXCEEDS_POSITION` when flat); an attached bracket is
   stored on the resting entry and its child exists only after the fill. None of them is
   reduce-only (`reduce_only: false`): a resting stop or bracket would open a position if the
   book were closed another way, so every exit must cancel the book's protection first
   (decision 7) and the I9 check must treat a stray protective order as an orphan.
5. Whether the `user` websocket channel carries futures order events with client ids.
6. Funding settlement instants, and whether `funding_pnl` is cumulative or daily: measured from
   the mirror history during the pilot.
7. Whether a perp maintenance window exists and how orders are rejected during it.
8. What a venue liquidation or ADL looks like in fills.

Each finding becomes a sanitized fixture and amends this ADR.

## P2-2 implementation boundary

`CoinbaseFuturesBroker` and `ProductRoutedBroker` are dormant adapters, constructed only in
hermetic tests. The API and worker still construct the unchanged spot broker. All live
futures starts remain `FUTURES_LIVE_UNSUPPORTED`, and `FUTURES_ORDER_PATHS` remains empty.
No order kind, schema migration, HTTP/CLI surface, or ops contract version changes here.

Every marketable reduction uses sized `close_position`; the `REDUCE_ONLY_MARKET` candidate and
its `FuturesReductionMode` constructor switch were removed once the venue refused reduce-only
(§1, probe 2026-10-16). There is **no automatic fallback**, no plain market reduction, and no
market entry. Post-only entry and standalone stop-limit and bracket orders were accepted by the
venue in the same probe (none are reduce-only); attached entry protection is still always
refused, with no constructor switch to enable it.

The unchanged `Broker` signature carries no purpose. Futures submission therefore requires
the managed client-id shape generated by `submit_intent` and validates its encoded purpose:
marketable orders accept only explicit exit purposes; stop-limit and bracket orders require
STOP and BRACKET respectively. There is no separate caller-supplied purpose override or
public submit-body helper. This is defense in depth, not authentication, and never replaces
persist-before-submit, product exclusivity, risk checks or the later runtime position check.

Domain quantities remain base-equivalent. The boundary requires positive integral contract
counts and exact Decimal division/multiplication. Overflow, underflow, nonfinite values and
inexact results deny even when caller traps are disabled; precision is not raised globally.
Pre-request submission refusals return REJECTED with zero broker transport sends. Unknown
catalog size is not one. Fills require explicit FUTURE/FILL, the expected product/order,
non-quote size and empty combo legs; commission is the authoritative all-in fill commission,
never the rounded itemization total. Fill timestamps require supported RFC3339 syntax with
an explicit known offset, valid offset hours/minutes and a representable UTC result. Local
hours/minutes/seconds are bounded to 00–23/00–59/00–59 before parsing; ISO hour 24 must not be
normalized into next-day evidence. Parsing happens once; microsecond precision is exact
(extra fractional digits must be zero), never silently truncated. Unknown offsets (`-00:00`),
leap seconds and out-of-range UTC instants
fail with `BrokerError`. Shared spot timestamp parsing is unchanged.
Malformed or incomplete history raises rather than
inventing zero or silently skipping rows. Ambiguous sends stay UNKNOWN through the existing
reconciliation contract; only definite HTTP or explicit venue refusals become REJECTED.

Product-bearing calls route directly. `get_order` and `cancel_order` have opaque venue/client
ids but no product, so the optional `OrderIdentityLookup` capability resolves a named venue
identity **before** selecting the parser or cancel adapter. Coinbase reads the historical
order, or scans at most 20 all-product pages for a client-only identity. A complete scan must
establish one unambiguous match; incomplete pagination fails closed, never guesses spot.
Every row must have a nonempty string client id, a nonempty venue id and consistent explicit
product/type before it can count as a nonmatch. There is no supported clientless scan-row
exception: missing, null, empty or nonstring client ids leave the scan unresolved, even after
a match or on a later page, and cannot authorize cancellation.
A known exact venue id is authoritative for **routing metadata**, not the caller's local
client label. Existing attached spot children have locally manufactured labels, never sent
as client ids; routing does not infer relationships from a `:child` suffix. The selected
adapter retains its own observation checks, including futures client-id echo matching.
Client-only resolution still requires the exact submitted client id.
There is no submit-time routing cache, so a fresh router works after restart. Adapters lacking
this capability fail closed on productless calls. Futures-specific client reconciliation is
also bounded, filtered to FUTURE and the product/submit window. Existing spot methods and
JSON helper bodies are preserved; the spot adapter only gains the optional identity reader.

## P2-3 implementation boundary

P2-3 publishes the optional live policy fields through the shared HTTP and operator models
and mirrored runtime CLI flags. Unset fields remain excluded from canonical policy JSON;
explicit `live_enabled: false` is retained. Ops contract v94 advertises
`live_futures_risk_policy`; the schema revision stays 0075 and `futures_order_paths` stays empty.
The CLI, HTTP and execution-service start refusals remain `FUTURES_LIVE_UNSUPPORTED`.

The pure admission gate requires fresh, finite venue facts, a known positive proposed initial
margin at start as well as entry, and matching bound/catalog contract size. Unknown position or
external-order reads deny; a pending/current recorded maintenance window denies. Only USD/USDC
policy reserves may cover CFM USD margin through the declared-peg threshold; USDT is unlinked.
Live book margin equity uses its allocation plus ledger equity. Shared loss breakers link only
managed same-mode books; live beta netting remains deferred.

Worker venue evidence, margin monitoring, alerts, fleet-health integration and switch-on remain
P2-4/P2-6/P2-7 respectively. Stored monitor/alert thresholds do not imply those paths are wired.
The [runtime skill](../../skills/thytrader-runtime/references/live-futures-policy.md) documents
publication, readback, field validation and the remaining operational boundary.

## P2-4 implementation boundary

P2-4 implements the **injected runtime**, not production venue acceptance. The default API
and worker venue builder still construct only the spot adapter (superseded for the worker by the
P2-7 wiring below). `compose_futures_venue` is an
internal process composition function accepting an already supplied broker and neutral evidence
source; no CLI, setting, boolean approval record or default builder calls it. Hermetic tests inject
a synthetic transport through the real futures adapter, intent store, worker step and fill ledger.
Those tests are not authentic Coinbase acceptance evidence. The §7 reduction/post-only probes,
P2-5 protection decision, P2-6 visibility and P2-7 acknowledgement/start gate remain prerequisites
for real wiring. All three `FUTURES_LIVE_UNSUPPORTED` refusals and empty `FUTURES_ORDER_PATHS`
remain unchanged. [ADR 0135](0135-durable-futures-safety-obligations.md) amends the original
no-migration assumption: Alembic 0076 persists independent safety duties, exposed as additive
report fields under ops contract v95. No new order kind or CLI flag.

### Evidence, units and funding

- Common book preparation loads live bindings, overnight margins and hourly funding. Live sizing
  uses allocation plus ledger equity, the futures fee tier and the binding's fixed fee; it never
  borrows spot base inventory or substitutes a spot fee/balance. A missing settled funding rate
  blocks a new live entry. Entries never attach an unverified bracket.
- A cycle shares one successful account observation across managed live futures books: two CFM
  GETs (`balance_summary`, `positions`), plus a separate complete futures open-order listing needed
  for I3. The fresh pair does **not** refresh killswitch evidence: that comes from the independently
  fresh persisted margin-window mirror. Unknown components remain unknown. External-order ownership
  requires exact product **and** durable client identity, not an id prefix. Spot-only fleets make
  no additional CFM reads. Completed stopped/flat books retire these reads after confirmation.
- Margin preparation and broker sizing share the same in-cycle catalog observation. Bound/catalog
  drift denies entries. Reductions use catalog-sized whole contracts and require signed venue/book
  agreement; a drift that makes quantities inconsistent or inexact cannot authorize a guessed exit.
- Live funding uses the existing unique-hour `futures_funding_entries` transaction and day-opening
  source `per_product_applied_fills_and_funding_v1`. It is **modeled accrued funding at a settled
  rate**, never an assertion of venue settlement. Invalid or unrepresentable live accrual/cash is
  missing evidence, not a stored NaN/zero. No USD amount is added to USDC.

### I9 and protective reductions

For I9, “no nonterminal order” means **no unsettled transaction**. Freshly observed OPEN,
unfilled resting managed protection is allowed; otherwise permanent protection would suppress
I9 for the entire holding period. Pending/UNKNOWN/cancel-pending/partially filled or unobserved
protection, dangling pending intents, incomplete accounting or unapplied fills still invalidate
a sample. The account observation must be strictly later than the last fill plus five seconds.
Only distinct increasing observations count. An unknown or equal comparison breaks the mismatch
run; rereading the same observation does not advance it. Two fresh valid mismatches persist the
existing pause. Restart clears only the in-memory counter, never the durable pause.

Independently proved flat/excess protection is cancelled before strict I9 sampling, including
partially filled OPEN protection; matching open-book protection is retained.
Flat book plus flat venue cancels confirmed orphan protection and records
`futures_orphan_order_cancelled`. A larger or opposite venue position is reduced only when its
**entire signed quantity** equals the exact applied managed-client fill ledger. Foreign, mixed,
unknown or fractional ownership is never reduced. After a repair the book remains paused.

A repair uses LIQUIDATION with a reserved `futures-flip:` idempotency identity. Its fill is checked
again inside the existing atomic fill transaction against the still-proven excess. Cash and fees
are applied while preserving the intended book projection: applying an ordinary exit delta would
incorrectly consume that valid projection twice. No inventory is adopted or manufactured, and an
opposite-side residual can only be reduced to venue-flat, not made to match by opening a new leg.
Memory and PostgreSQL tests cover this exceptional accounting path. An invalid repair identity,
quantity or proof fails the transaction closed.

The margin monitor runs before bar/entry gates, including paused books, managed stop, fleet disarm,
venue killswitch and `live_enabled: false`. The ratio comparison uses exact multiplication, not
rounded display ratios. A triggered de-risk remains durable while cancellation or submission is
uncertain, even if the margin recovers. Confirmed protection cancellation precedes the reduction;
an active/ambiguous reduction is reconciled, never cancelled-and-replaced or resent. A completed
de-risk pauses entries for review (a stopped book stays stopped).

Live futures exits use an explicit price-free reduction with a decision instant, not fabricated
OHLC candles. The existing intent `candle_starts_at` stores that instant for this narrow path;
it is not evidence that a candle existed. A latest mark is optional audit context only. Missing
bars, funding, fees and entry permission do not trap a known reducible position; unknown position,
quantity or ownership does. Every live futures close, including a natural protective fill during
managed shutdown or a close before the stop command, retains a typed durable confirmation duty (not
merely the `FUTURES_FLAT_CONFIRMATION_PENDING` diagnostic) until a
later fresh post-fill CFM read confirms flat, then audits `futures_flat_confirmed`. Ordinary spot
and paper retain their candle-priced paths. Uncertain sends recover through FUTURE client scans
and canonical fills using the existing persist-before-send contract. If a process dies after
committing the intent but before its local order, the fenced worker restores an UNKNOWN local
observation anchor with that same client identity and original submit window. It scans rather
than submits; absence is not permission to resend. Stopped-flat books with such unresolved
intents remain supervised.

## P2-7 implementation boundary: production wiring

P2-7 lands in two changes. The first wires the worker; the second opens the acknowledged
live start (fees, capital, HTTP 428, CLI, `FUTURES_ORDER_PATHS`).

- The worker's venue builder composes, for every credentialed generation, a
  `CoinbaseFuturesBroker` and a `RecordedFuturesSource` on that generation's instrumented REST
  client (`coinbase_futures_extension` in `execution_worker/futures_venue.py`, the only place
  either is constructed). Credential hot-swaps rebuild both with the spot bindings.
- **Routing is per book, not process-wide.** `ExecutionVenue.live_broker` stays the plain spot
  adapter, so spot books keep exactly their REST calls and failure modes. A separate
  `futures_live_broker` (`ProductRoutedBroker(spot, futures)`) is handed only to live books
  on a futures product (`book_live_broker`). The router resolves every opaque get/cancel with an
  extra venue read, which spot books must not pay. A live futures book without a composed
  futures broker gets none and pauses as "Live broker is unavailable"; it never falls back to
  the spot adapter. Paper books never submit through either.
- The API builds no futures adapter: live futures stop and flatten run in the worker.
- Composition places nothing. CFM reads happen only while a live futures book exists (spot-only
  fleets make none), and the live start remains refused (`FUTURES_LIVE_UNSUPPORTED`,
  `FUTURES_ORDER_PATHS` empty) until the second change.
- This supersedes the "routing keeps every caller unchanged" rationale below for spot books: they
  keep the unrouted adapter.

## Consequences

- Live futures reuse the existing intent, reconciliation, protection and exit machinery; the
  new risk is concentrated in one broker, one gate module, the I9 check and the margin
  monitor.
- Nothing is reachable until P2-7, and P2-6 visibility lands before it: every new check has a
  report, alert or banner before an operator can start a live futures book.
- Product exclusivity (I3) is a hard constraint: manual trading of a managed product pauses
  the book. It also makes the account-level reduction of §1 correct.
- Live spot entries depend on the declared reserve while managed futures are open.
- Funding is modeled, not settled, until P2-10; drift is alerted, not corrected.
- Dated contracts, hedge mode and venue-originated fills stay out of the pilot.

## Alternatives considered

- **A new `OrderKind.CLOSE_POSITION`.** More explicit, but needs a CHECK migration and a review
  of every marketable call site for no behavioral gain; rejected while all exits share one
  path.
- **Plain market orders to reduce.** Rejected: without venue-enforced reduce-only they can open
  or flip a position when the account is not where the book thinks it is. Reduce-only market
  orders were the other candidate; the venue refuses them (§1, probe 2026-10-16).
- **A second live broker per venue instead of product routing.** Rejected: reconciliation and
  exits address one broker; routing by product id keeps every caller unchanged.
- **Sizing live futures from ledger equity alone.** Rejected: a live ledger starts at 0
  (ADR 0106), which denies every entry; the allocation is the capital basis.
- **Shipping a web start form in P2.** Deferred (§6f); the CLI and HTTP surfaces carry the hard
  acknowledgement gate first.
