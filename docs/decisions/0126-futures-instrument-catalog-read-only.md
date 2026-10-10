# 0126: Coinbase futures instrument catalog, funding history and candles (read-only)

- Status: Accepted
- Date: 2026-10-10
- Relates to: [0031](0031-coinbase-first-platform-end-state.md),
  [0045](0045-spot-shorting-and-attached-entry-brackets.md),
  [0056](0056-multi-instrument-documents-and-pyramiding.md),
  [0085](0085-fast-research-ingest.md),
  [0089](0089-agent-research-ergonomics.md),
  [0095](0095-sparse-markets-no-trade-bars-listing-floors.md),
  [0125](0125-correlation-aware-risk-limits.md)
- Supersedes in part: the "futures stay out" wording of 0045 and 0056, for **data and
  read-only surfaces only**. Every order, execution, live, adoption and discretionary rule of
  those ADRs is unchanged.

## Context

Brayden has Coinbase Financial Markets (CFM) US futures enabled. Perp-style contracts (for
example `BIP-20DEC30-CDE`, nano BTC) charge funding every hour, and the venue publishes only
the current rate: there is no public funding history, so history starts when ThyTrader starts
recording it. A later phase may backtest, paper-trade and finally hedge with futures; this
ADR covers phase P0, which only observes.

Futures rows differ from spot rows in ways the spot code would mishandle:

- `base_currency_id` is `""` and `quote_min_size` is `"0"`, so the spot parser rejects them.
- Ids fail `SPOT_PRODUCT_ID_PATTERN`, which guards about 30 execution and live sites.
- The id prefix is a contract code, not the underlying: `BIP` is BTC and `SLP` is SOL.
- Every row reports `contract_expiry_type: EXPIRING`. Perps report a far-future sentinel
  (`2089-12-30`) as `contract_expiry` while the id says `20DEC30`.
- `base_increment` and `base_min_size` are in contracts; `contract_size` is the underlying
  quantity per contract (0.01 BTC for `BIP`).
- Index, energy and some metals contracts do not trade 24/7.

## Decision

### 1. P0 is read-only

There is no futures order path in P0. This is enforced structurally, not by convention:

- `SPOT_PRODUCT_ID_PATTERN` stays on every execution, live, adoption and discretionary
  surface, so a futures id cannot form an order intent.
- `CoinbaseRestBroker.place_order` refuses any non-spot product id with `BrokerError`
  before any request.
- The CFM account adapter (slice P0-5, ADR 0127) allowlists GET paths only.

### 2. A separate instrument model and parser

- `market_data/instruments.py` holds the provider-neutral model: `InstrumentKind`
  (`spot`, `dated_future`, `perpetual_future`), `FuturesProduct` (contract code,
  `underlying` from `contract_root_unit`, settlement currency `USD`, contract size, exact
  increments in contracts, venue expiry and id-listed expiry, 24/7 flag, intraday and
  overnight long/short margin rates, funding, session and maintenance window), `Instrument`
  and `InstrumentCatalog`.
- `market_data/instrument_ids.py` holds `FUTURES_PRODUCT_ID_PATTERN`
  (`^[A-Z0-9]{2,6}-\d{2}[A-Z]{3}\d{2}-CDE$`) and `MARKET_PRODUCT_ID_PATTERN` (spot or
  futures), which is for read-only data surfaces only.
- `exchanges/coinbase_futures_catalog.py` parses FCM rows. The spot parser is not loosened.
  INTX rows (retired international perps) are excluded.
- **Perps** are detected by a non-empty `funding_interval`, not by expiry type.
  `FuturesProduct.expires_at` is `None` for a perp; the venue sentinel is kept as
  `venue_expiry_at` and the id day as `listed_expiry`.
- **Unknown is never zero.** An unlisted margin rate, funding rate or session fact is
  `None`.

### 3. Listing completeness

The listing is read from the public `GET /market/products?product_type=FUTURE` endpoint in
pages of 50 by `offset`. Verified live on 2026-10-10: `num_products` echoes the page's row
count rather than a total, `has_next` is honest under paging, and
`expiring_contract_status=STATUS_ALL` returns 242 rows, so the unexpired 100-row answer is
not a silent cap. Each page must still prove coverage: a page larger than requested, a full
page with no `has_next`, an empty page that claims more, a repeated id, or a listing that
does not end within 20 pages fails the whole read. A partial futures catalog is never
returned.

### 4. Fingerprints stay byte-identical

The spot `ProductCatalogSnapshot`, `list_enabled_spot_products` and `enabled_spot_product`
are unchanged. `MarketDataService` gains an optional `futures_provider`, a separately cached
and separately fingerprinted futures listing, `instrument_catalog()` (spot and futures side by
side, each with its own fingerprint) and `enabled_instrument(product_id)`. Without a futures
provider (demo mode) the catalog is spot-only and a futures id is unknown. Strategy, run-spec,
dataset and result fingerprints do not change; golden tests pin the spot catalog fingerprint.

### 5. Funding history and contract observations (P0-3)

- The market-data worker polls the public futures listing every 5 minutes in Coinbase mode,
  with an unauthenticated client (the endpoint is public). Demo mode records nothing.
- `futures_catalog_poll_state` records the last attempt, last success, failure streak and
  failure code, so a stopped or failing poller is visible.
- `futures_instrument_observations` stores a new row only when a contract's payload
  fingerprint changes (increments, margin rates, 24/7 flag, session state, maintenance window,
  enablement); unchanged polls extend `last_seen_at`. Funding and per-session open/close
  instants are excluded from the fingerprint.
- `futures_funding_rates` keys on `(product_id, funding_time)`. Live evidence on 2026-10-10
  (00:48Z) showed the listing's `funding_time` is the most recent funding hour (00:00Z), not
  the next one, so the plan's "last observation strictly before `funding_time`" rule cannot
  apply. Instead, an hour is **current** while the listing names it and **settled** once the
  listing names a later hour for that contract. The settled rate is the last value observed
  while the hour was current (`revision_count` counts changes while current). A settled row is
  immutable: a later different value increments `conflict_count`, records the value and time,
  writes a `futures_funding_conflict` audit event, and is never applied. A perp listed without
  a rate or time contributes nothing (never a zero).
- Hours after a contract's first recorded hour with no row are gaps. They are reported by the
  operator `funding` report and never filled. Migration 0071's downgrade refuses while any
  funding row exists, because the history cannot be re-fetched.
- `thytrader-operator funding [--product-id ID] [--hours 1..720]` (`GET
  /api/v1/operator/funding`) reports poller health, per-contract coverage, gaps and
  conflicts. Ops contract v74 adds `instrument_kinds`, the empty `futures_order_paths` and
  `futures_observations`.
- A conflict is surfaced through the audit log and the report, not through the durable alert
  feed of ADR 0115, whose supervision runs in the execution worker over trading state.

### 6. Futures candles (P0-4)

The data lane (watch, ingest, catalog, verify) accepts `MARKET_PRODUCT_ID_PATTERN`. Only
24/7 contracts are accepted; others are refused with `INSTRUMENT_SESSIONS_UNSUPPORTED`,
because the dataset model has no session calendar. Futures volume stays in raw venue units
(contracts) and a futures dataset manifest says so with `volume_unit: "contracts"`; spot
manifests are byte-identical. A dated contract's watch target retires at expiry.

### 7. Currency scope

CFM settles in USD. A futures amount is never added to a USDC or USDT amount anywhere,
including the portfolio report.

## Consequences

- Funding history accrues from the day the poller is deployed; earlier perp windows cannot be
  backtested without a separately fingerprinted vendor import.
- Read surfaces can show futures; every order surface still cannot reach one.
- The spot catalog cache and the futures cache can disagree in age by up to 30 seconds; the
  combined catalog reports the older `observed_at`.

## Alternatives considered

- **Loosen the spot parser.** Rejected: it would let futures rows into spot surfaces and
  change the spot catalog fingerprint.
- **Fail closed on exactly 100 rows.** Rejected after the live check above: paging by offset
  with `has_next` proves coverage directly, while a row-count heuristic would break when the
  listing grows.
- **Derive the underlying from the id prefix.** Rejected: `BIP`, `ETP` and `SLP` are codes.
