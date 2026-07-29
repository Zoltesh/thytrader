# Market-Data Pipeline

## Current implemented increment: supervised historical ingestion

ThyTrader currently exposes read-only USD-spot **data-source diagnostics**—a product catalog, a
bounded recent candle-validation window, and a seven-day 1h range-completeness report—at:

```text
GET /api/v1/market-data/products
GET /api/v1/market-data/preview?product_id=BTC-USD
GET /api/v1/market-data/range?product_id=BTC-USD
GET /api/v1/market-data/ingestion?product_id=BTC-USD
```

The range endpoint paginates through Coinbase's 350-candle limit using non-overlapping pages,
validates every candle for UTC alignment, chronological order, OHLC consistency, and decimal
exactness, and reports expected vs received candle counts, gaps, and a binary completeness result.
It is bounded to 2,160 candles (90 days at 1h) and cannot request ranges ending in the future.

These are intentionally **diagnostics**, not a historical-dataset service, chart, or strategy input:

- With Coinbase credentials, it reads current product constraints and a bounded recent candle window
  through the official Coinbase Advanced Trade SDK.
- Without credentials, it uses deterministic demo candles so a clean local install remains usable
  without external network access.
- It normalizes data into provider-neutral `MarketProduct` and `Candle` models.
- All prices, sizes, and volume remain `Decimal` values until the explicitly serialized browser
  boundary.
- It excludes incomplete/current candles, sorts completed candles, counts discontinuities and
  missing intervals, and marks a preview stale after two expected intervals.
- Malformed upstream product/candle payloads fail the complete request rather than silently
  returning partial or repaired data.
- The dashboard visibly distinguishes complete, gap-detected, stale, and unavailable data.

The first three responses report request-time data-source facts. The ingestion endpoint separately
reports durable evidence from the supervised worker: last attempt/success, requested and verified
coverage, freshness, immutable fingerprint, and stable redacted failure state. None assert that the
market is safe to trade.

## Current scope boundary

The current preview supports:

| Dimension | Current support |
|---|---|
| Provider | Coinbase Advanced Trade |
| Product | Enabled Coinbase USD spot products; deterministic demo: `BTC-USD`, `ETH-USD`, `SOL-USD` |
| Timeframe | `1h` |
| Data access | Bounded recent REST request or deterministic demo |
| Persistence | Complete validated ranges only, through the dedicated worker |
| Trading use | None |

The catalog is presentation-only. It filters to enabled USD spot products and exposes venue
constraints as exact decimal strings. The preview accepts the selected catalog product through a
validated `product_id` query parameter; it is still not a complete historical API or execution
input.
No strategy, backtest, paper session, or live trading path may depend on the preview endpoint.

## Quality semantics

### Closed candles only

A candle becomes eligible only when `starts_at + interval <= observation_time`. The current open
candle is excluded because its OHLCV values are mutable and could introduce lookahead bias.

### Completeness

For adjacent completed bars, a delta greater than one interval creates one visible gap and adds the
number of skipped intervals to `missing_intervals`. The system does not interpolate missing prices.

### Freshness

`stale` means the latest completed candle is more than two expected intervals older than the
observation instant. It is a data-freshness fact—not a worker-health claim.

### Fail closed

The adapter rejects naive/non-UTC timestamps, duplicate or off-interval timestamps, invalid decimal
strings, non-finite values, negative volume, invalid venue increments, and internally inconsistent
OHLC values. Returning a partial candle set would overstate its quality.

## Immutable local dataset contract

A complete validated 1h range can now be written through the internal `DatasetStore` as immutable,
date-partitioned Parquet plus a JSON manifest. The writer rejects incomplete ranges before creating
any published dataset. It stores decimal fields as exact strings at the analytical boundary and uses
a SHA-256 fingerprint over the schema, provider/product identity, requested range, completeness facts,
and canonical candle content.

Each Parquet file is flushed and atomically renamed, then its directory is synchronized before the
next publication step. The final manifest is likewise flushed, atomically renamed, and its directory
synchronized last. A manifest at its canonical `manifests/<content-sha256>.json` path is the sole
publication marker: a crash can leave undiscoverable orphan files, but it cannot publish a partial
dataset. Existing unmanifested files cause a safe failure rather than being reused.

`DatasetStore.load_verified()` accepts only a complete 1h manifest at that canonical fingerprint path.
It validates identifier/time/count facts, resolved paths beneath the configured root, and complete
candle coverage after reading every referenced Parquet file; it then recomputes the fingerprint before
returning a dataset to a future backtest or worker.

```text
<dataset-root>/
  coinbase/BTC-USD/1h/2026/07/01/part-<content-sha256>.parquet
  manifests/<content-sha256>.json
```

The manifest records schema version, provider, product, timeframe, requested range, expected and
received counts, gap/missing facts, completion outcome, fingerprint, and relative Parquet files.
The internal writer is deliberately not an API mutation endpoint. The separately supervised
`thytrader-market-data-worker` process is the only component that turns validated provider ranges into
durable datasets. It is distinct from `thytrader-worker`, which records portfolio valuation history.

## Worker lifecycle and durable diagnostics

The market-data worker aligns each request to the last complete UTC hour, requests a configurable
bounded 1h lookback through `MarketDataService`, and records the attempt in PostgreSQL before provider
I/O. It calls `DatasetStore.write()` only when the returned report exactly matches the requested range
and is complete, contiguous, and gap-free. It then calls `load_verified()` before recording success.

Failures retain prior verified coverage while recording a stable redacted code/message and consecutive
failure count. A later successful retry clears failure state. PostgreSQL is authoritative for this
operational state; Parquet remains authoritative for immutable candle content. Compose supervises this
process independently with its own readiness marker, restart policy, and persistent dataset volume.
Demo ingestion is explicitly identified as provider `demo`; it never masquerades as Coinbase data.

Whenever the aligned hourly boundary advances, the worker publishes a distinct immutable lookback
window.
Identical retries of the same window are idempotent, but overlapping windows with different start/end
bounds intentionally have different fingerprints and remain independently verifiable. ThyTrader does
not automatically prune published manifests or Parquet partitions yet: safe retention requires a
dataset catalog and reference tracking so an operator cannot delete data that a future reproducible
backtest names by fingerprint. Until that contract exists, operators must size the dataset volume for
continued accumulation and treat manual deletion as destructive maintenance performed only while all
ThyTrader processes are stopped and after preserving any required fingerprints.

This worker has no portfolio-history, strategy, backtest, paper/live trading, broker, transfer,
withdrawal, leverage, derivatives, or optimization authority.

## Next increments

The diagnostics create a tested boundary to expand rather than a side path to maintain.

1. **Durable historical ingestion** — extend range ingestion to additional timeframes (5m, 15m, 30m,
   6h, 1d) and persist validated ranges as immutable partitioned Parquet datasets with schema
   versioning, source range, completeness facts, and content/dataset fingerprints.
2. **Additional ingestion targets** — expand the worker from one configured 1h product/range to
   explicitly managed products and supported timeframes without weakening complete-only publication.
3. **Diagnostics contract** — provide versioned machine-readable market-data health/data coverage
   reports and a CLI before adding commands to `thytrader-operator/SKILL.md`.

Only a validated, immutable dataset with a fingerprint may become a Phase 3 backtest input.
