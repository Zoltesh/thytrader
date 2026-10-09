# 0124: Inventory adoption of held coins by live books

- Status: Accepted
- Date: 2026-10-09
- Relates to: [0058](0058-protection-lifecycle-accounting.md),
  [0106](0106-account-risk-capital-and-live-startup-baselines.md),
  [0107](0107-capital-normalized-live-performance.md),
  [0113](0113-deploy-anchored-window-cache.md),
  [0114](0114-readiness-preflight-and-venue-reconciliation.md),
  [0117](0117-truthful-inventory-and-fleet-controls.md),
  [0119](0119-venue-order-observation-provenance.md),
  [0120](0120-verified-risk-opening-evidence.md)

## Context

The Coinbase account often holds coins that no book manages, such as coins bought by hand or
left by a retired bot. Venue reconciliation reports them as external inventory (ADR 0114). No
bot or discretionary book can protect them, and the operator cannot sell them through
ThyTrader to USDC. Buying them again would double the exposure and pay fees for nothing.
Selling by hand bypasses the journal and the risk accounting.

The runtime assumed that every order it records went to a venue. An order with no venue id
reads as an unconfirmed submit. A broker routes any unrecognised order kind as a real
post-only limit order. A book with no entry intent cancelled every working non-protective
order on pause, including its exits. Unprojected-inventory detection anchored only on entry
intents.

## Decision

A live book can **adopt** coins already held at the venue: it takes ownership of them at a
mark, without any venue order. The feature is live-only in v1. A paper adoption would be
inventory the venue does not hold, and it would distort the paper results used for
promotion, so paper requests get 409 `ADOPTION_LIVE_ONLY`. Seeding a paper twin may come
later.

### Domain model

- **Enums.** `OrderKind.ADOPTION` says how the inventory was opened: never routed.
  `IntentPurpose.ADOPTION` says why. `INVENTORY_OPENING_PURPOSES` is `{ENTRY, ADOPTION}`.
- **Records.** One adoption is an intent, a FILLED order and one fill:
  - side buy, `price` equal to the mark, no stop or take-profit on the order;
  - `filled_quantity` equals `quantity`, there is no venue id, and the client id is
    `adopt:{deployment}:{uuid7}`;
  - the fill has fee 0, `filled_at` now and `economics_applied_at` now;
  - no venue observation time is recorded (ADR 0119).
- **Projection.** The adoption is projected through the ordinary buy-fill projection in the
  same transaction that writes the records. The stop and target are staged as the book's
  pending levels first, so `_project_entry` takes the book from FLAT to OPEN:
  - `entered_bar` is the bucket of the adoption instant;
  - `trail_extreme` starts empty;
  - `last_evaluated_bar` moves to the mark bar, so that bar is not evaluated again.

  A live strategy ledger starts at cash 0 (ADR 0106), so the adoption debits cash by its
  notional and the book's equity at the mark is 0, exactly as after a live buy.
- **Never routed.** The Coinbase and paper brokers raise on `OrderKind.ADOPTION`.
  `submit_intent` refuses an adoption before writing anything.

### An adoption is not a venue order

- **Reconcile** never asks the venue about an adoption. A missing, unapplied, partial or
  over-covering adoption fill pauses the book with `ADOPTION_EVIDENCE_INCOMPLETE`.
  `replay_unapplied_fills` never projects an unapplied adoption fill: the book's current
  pending levels would otherwise become its stop.
- **Not an entry.** An adoption consumes no entry-rate budget and counts as no venue action.
  Protection placed afterwards counts normally.
- **Pause cancels only entries.** `cancel_risk_increasing_orders` and the working-entry
  fallback skip any order whose intent is known and is not ENTRY. Before this change, a book
  with no ENTRY intent cancelled its working marketable exits on pause and could treat a
  working exit as its entry.
- **Unprojected inventory** anchors on `INVENTORY_OPENING_PURPOSES`. Otherwise adopting,
  adding a pyramid entry and then partly exiting raises a false fault.
- **Protection and restart** are unchanged. The worker finds an OPEN book with a position
  and places the bracket or stop-limit on its next cycle.
- **Exposure** counts quantity times the adoption price. A FILLED adoption reserves no
  working-entry quote.
- **Opening evidence.** An adoption fill qualifies as opening evidence under ADR 0120, so an
  intraday adoption keeps the day's opening equity at the initial funding.
- **Execution quality.**
  - An adoption fill opens its round trip at the mark, so trips and the ledger agree.
  - It is flagged `adopted` and has no liquidity and no slippage benchmark.
  - It adds no evidence reason and is left out of the slippage fill counts and the twin fee
    normalization.
  - `totals.adopted_fill_count` reports how many there are.
  - A twin with adopted inventory reports different fill populations, so backtest and paper
    comparisons do not treat adopted lots as signals.
- **Decision journal.** No bar row is written for an adoption. A bar whose window contains
  one classifies as holding.
- **Why-trade journal.** `TradeReasonSignalKind.ADOPTION` and the `adoption` purpose record
  one row per adoption, with the risk verdict and provenance notes.
- **Venue reconciliation.** The adopted quantity becomes managed inventory, and
  `EXTERNAL_INVENTORY` shrinks by it.

### Quantity, mark and serialisation

- **Adoptable quantity.**
  - unmanaged = venue total − Σ managed long quantity − Σ unfilled remainder of working
    buy orders on the base − Σ unfilled remainder of working short-entry sells.
  - adoptable = min(available, unmanaged), rounded down to the product's base increment.
  - Coinbase `available` already excludes base held by resting protective sells, so those
    are not subtracted twice.
  - Adoption is refused with `ADOPTION_BASE_UNRESOLVED` when any live book on the base has an
    UNKNOWN order, unsettled fills, unresolved accounting, or duplicate balance rows.
    Unknown is never zero.
- **Order of reads.** Take the lock, read local claims, read the venue balance, then write.
  A sell that lands in between can only shrink the result. A buy can only grow it if its
  intent is inserted mid-section, which the shared lock prevents.
- **Locks.**
  - PostgreSQL takes `pg_advisory_xact_lock` on `thytrader:inventory:live:<base>`, with a
    short `lock_timeout` around the venue read.
  - Every live ENTRY intent takes the same key's shared lock.
  - The memory store uses one `asyncio.Lock` per (mode, base).
- **Store.** Claims are computed by a pure `trading/inventory_claims.py`, because the
  operator package sits above execution. Adoption writes go through a separate
  `InventoryAdoptionStore` protocol with an `AdoptionWrite` model, kept off the overlay,
  leased and disabled store wrappers. The PostgreSQL implementation lives in its own module.
- **Mark.** The mark is the last closed candle of the book's timeframe, checked with the same
  freshness prerequisites as a discretionary entry. An `inventory_adopted` audit event
  records the mark source, the candle start and the balance figures.

### Surfaces

- **Discretionary protect.**
  - Takes a quantity (a number or `all`), a stop, an optional take-profit and an
    idempotency key, with the live acknowledgement.
  - It is refused while an occupied discretionary book exists for the product.
  - It is allowed under fleet disarm, because it only adds protection.
- **Sell holdings.**
  - Creates the book STOPPED with lifecycle FLATTEN in the same write.
  - A sentinel stop of one price increment satisfies the NOT NULL stop. FLATTEN takes
    priority over protection, so no protective order is ever submitted.
  - The stopped-residual flatten then sells the coins.
  - It reduces risk, so it is not entry-gated.
- **Strategy start with adoption.**
  - v1 covers single-instrument, long-only strategies.
  - The deployment insert and the adoption commit in one transaction, so a FLAT running bot
    can never start buying with its allocation after a failed adoption.
  - Strategy adoption respects the fleet latch like an entry.
  - The stop and target come from the strategy's exits and the ATR of the deploy-anchored
    window the worker evaluates. The shared closed-window loader moved to
    `execution/closed_windows.py` for this.
  - Performance capital is pinned to max(allocation, adopted notional), so ADR 0107
    percentages stay meaningful.
- **Risk.** `ProposedEntry.funding` is `quote` or `in_kind`. An in-kind entry adds its
  notional to capital and skips order bounds, the rate limits and the price collar, because
  nothing goes to the venue and no quote is spent. It keeps the membership, exposure,
  allocation, daily-loss and drawdown checks.
- **Short sales.** The short-sale check in the API and the worker uses the unmanaged-base
  quantity instead of raw `available`. A short can then no longer sell base that a managed
  long owns but has not yet protected.
- **Agent and UI.**
  - HTTP: a read-only preview, a confirmation-gated POST, and `adopt_holdings` on deployment
    create.
  - CLI: `place-order --entry-kind adopt`, `sell-holdings`, `start --adopt-holdings` and
    `adoption-preview`.
  - Operator-chat tools, plus Trade-page and Holdings actions in the web UI.
  - Each surface ships with its skills, operator schema and CLI help.

### Persistence

Migration **0070** (predecessor 0069) is constraint-only. It widens `ck_order_intents_kind`,
`ck_trade_reason_purpose` and `ck_trade_reason_signal_kind` to admit `adoption`.
`order_intents.purpose`, `execution_orders.kind` and the decision payloads have no CHECK.
No column is added. The downgrade refuses while any adoption intent, order or why-trade row
exists: older code cannot represent an adopted book, and its brokers would route an unknown
order kind as a post-only limit. The ops contract moves to v69.

## Consequences

- **Daily loss.** Adopted losses count toward the daily-loss and drawdown breakers, which is
  truthful. An adopted lot marked down on the day it was taken can stop new entries.
- **Execution quality.** An adopted book's report is complete without inventing a fill-time
  benchmark, and its twin comparison discloses the population difference.
- **Rollout.** The domain, the migration and the bug fixes land first, with no way to create
  an adoption. The claims store and locks, risk funding, the discretionary and strategy
  surfaces, and the web UI follow as separate changes, each with its own tests and
  operator docs.
- **Rollback.** Rolling back past 0070 needs every adoption row removed first.

## Alternatives considered

- **Re-buy the coins with a real order.** This pays fees and slippage, briefly doubles
  exposure, and still leaves the held coins unmanaged. Rejected.
- **Record adoption as an ENTRY purpose.** It would consume the entry-rate cap and be
  cancelled as risk-increasing. It would also look like a strategy signal in comparisons.
  Rejected; a separate purpose and kind keep "why" and "how" truthful.
- **Leave adoption fills out of the execution-quality fold entirely.** The adopted lot's exit
  would then open a phantom short cycle, and the ledger delta would absorb the adoption.
  Rejected in favour of flagging the fill.
- **Allow declared paper adoptions.** These are invented inventory that the venue never held.
  Rejected for v1.
- **Require a `--stop-price` for sell-holdings.** This forces an operator to invent protection
  for coins they are selling. Rejected in favour of the sentinel stop under FLATTEN, which is
  proven never to be submitted.
- **Reuse the operator's managed-inventory collector.** The operator package sits in a higher
  layer than execution. Rejected for a pure claims module in `trading`.
