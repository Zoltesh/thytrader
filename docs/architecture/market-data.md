# Market-Data Pipeline

## Current implemented increment: read-only preview

ThyTrader currently exposes a narrow, read-only USD-spot catalog and hourly market-data preview at:

```text
GET /api/v1/market-data/products
GET /api/v1/market-data/preview?product_id=BTC-USD
```

It is intentionally a **validation preview**, not a historical-dataset service:

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

The response reports observable data facts only. It does **not** assert that a worker is running,
that data is durably stored, or that the market is safe to trade.

## Current scope boundary

The current preview supports:

| Dimension | Current support |
|---|---|
| Provider | Coinbase Advanced Trade |
| Product | Enabled Coinbase USD spot products; deterministic demo: `BTC-USD`, `ETH-USD`, `SOL-USD` |
| Timeframe | `1h` |
| Data access | Bounded recent REST request or deterministic demo |
| Persistence | None |
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

## Next increments

The preview creates a tested contract to expand rather than a side path to maintain.

1. **Product catalog** — ingest/query Coinbase spot products with tradability and venue constraints;
   do not hard-code a single product.
2. **Historical range ingestion** — support documented Coinbase request windows and pagination for
   5m, 15m, 30m, 1h, 6h, and 1d closed candles.
3. **Immutable Parquet datasets** — partition by provider/product/timeframe/date and record a schema
   version, source range, completeness facts, and content/dataset fingerprint.
4. **Market-data worker** — supervise scheduled ingestion separately from portfolio snapshots; log
   redacted failures, retry safely, and expose durable freshness/coverage state.
5. **Read-only diagnostics contract** — provide versioned machine-readable market-data health/data
   coverage reports and a CLI before adding commands to `thytrader-operator/SKILL.md`.

Only a validated, immutable dataset with a fingerprint may become a Phase 3 backtest input.
