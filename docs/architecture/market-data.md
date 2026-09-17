# Market-Data Pipeline

## Current implemented increment: supervised historical ingestion

ThyTrader currently exposes read-only USD-spot **data-source diagnostics**—a product catalog, a
bounded recent candle-validation window, and a seven-day 1h range-completeness report—at:

```text
GET /api/v1/market-data/products
GET /api/v1/market-data/preview?product_id=BTC-USD
GET /api/v1/market-data/range?product_id=BTC-USD
GET /api/v1/market-data/datasets
GET /api/v1/market-data/datasets/latest
GET /api/v1/market-data/ingestion?product_id=BTC-USD
GET /api/v1/market-data/freshness?product_id=BTC-USD
GET /api/v1/market-data/feed?product_id=BTC-USD
```

The range endpoint paginates through Coinbase's 350-candle limit using **inclusive** page ends
(Coinbase `end` is the last closed candle start, not an exclusive bound). Each page requests at most
350 bars, keeps candles whose `starts_at` lies in `[page_start, inclusive_end]`, and advances
`page_start` to `inclusive_end + duration`. Exclusive paging dropped the oldest bar on a full 5m
page. The adapter still validates every candle for UTC alignment, chronological order, OHLC
consistency, and decimal exactness, and reports expected vs received candle counts, gaps, and a
binary completeness result. It is bounded to 129,600 candles (90 days at 1m). One-hour watches stay
`min(requested, 2,160 hours)` and cannot request ranges ending in the future.

The worker maintains immutable, fingerprint-addressed 1h, 5m, 15m, 30m, 6h, 1d, 1m, 2h, and 4h historical datasets. Initial
backfill publishes complete UTC-day chunks oldest-first; incomplete days are classified holes and
are never interpolated. When the watch lookback starts before `covered_starts_at` of a complete
island, the worker prepends complete UTC-day chunks newest-first (`prefix_backfill`) and stops at
the first hole. Latest verified coverage is the newest contiguous complete island. `complete` describes only that
island. `watch_complete` is the agent completion decision: it is true only when the complete island
spans the configured half-open watch window. Catalog, ingest status, gap inspection, and operator
data-catalog payloads put `watch_complete` on the decision surface before `complete`.
`GET /api/v1/market-data/datasets` and `/datasets/latest` list fingerprint-addressed island
publications; they are not a watch-completeness surface.
`inspect-gaps` classifies missing bars across the full watch window as `not_fetched`,
`exchange_unavailable`, or `incomplete_local`; it never interpolates, and a clean short island does
not produce `gap_count: 0` for an incomplete watch.
`POST /api/v1/data/ingest` queues a watchlist ingest job (HTTP 202) and does not call `ingest_once`.
The market-data worker is the only publisher. The API Compose volume stays `:ro`. Preview/range
endpoints remain diagnostics, not strategy inputs. The worker clears `ingest_requested_at` after
`ingest_once` returns, so CLI polling waits for the walk, not only for queue acceptance.

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
- The dashboard visibly distinguishes complete, gap-detected, watch-incomplete, stale, and unavailable data.

The first three responses report request-time data-source facts. The ingestion endpoint separately
reports durable evidence from the supervised worker: last attempt/success, requested and verified
coverage, freshness, immutable fingerprint, and stable redacted failure state. The dedicated
`freshness` endpoint makes the newest verified-candle age explicit (<2h05m is fresh), while the
`feed` endpoint reports the public WebSocket ticker lifecycle independently from candle freshness.
None assert that the market is safe to trade.

## Current scope boundary

The current preview supports:

| Dimension | Current support |
|---|---|
| Provider | Coinbase Advanced Trade |
| Product | Enabled Coinbase USD and USDC spot products; deterministic demo: `BTC-USD`, `ETH-USD`, `SOL-USD` |
| Timeframe | `1h`, `5m`, `15m`, `30m`, `6h`, `1d`, `1m`, `2h`, and `4h` for complete-only datasets, strategy LTF, paper, live, discretionary books, and HTF tokens ([ADR 0040](../decisions/0040-venue-strategy-paper-live-htf-clocks.md)). Paper and live evaluate `htf_filter` on last-completed complete-only HTF bars ([ADR 0041](../decisions/0041-paper-live-htf-filter-evaluation.md)). Extra-TF LTF-list indicators use the same last-completed complete-only bars ([ADR 0042](../decisions/0042-per-indicator-timeframes.md)). Missing bars are never interpolated. |
| Data access | Bounded recent REST request or deterministic demo |
| Persistence | Complete validated ranges only, through the dedicated worker |
| Trading use | None |

The catalog is presentation-only. It filters to enabled USD and USDC spot products and exposes venue
constraints as exact decimal strings. The preview accepts the selected catalog product through a
validated `product_id` query parameter; it is still not a complete historical API or execution
input.
No strategy, backtest, paper session, or live trading path may depend on the preview endpoint.

## Quality semantics

### Closed candles only

A candle becomes eligible only when `starts_at + interval <= observation_time`. The current open
candle is excluded because its OHLCV values are mutable and could introduce lookahead bias.
If that completion boundary cannot be represented by the timestamp type, quality analysis rejects
the candle with a controlled `CandleQualityError` rather than leaking a raw datetime overflow.

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
Provider pagination, demo ranges, worker scheduling, dataset verification, and read-only diagnostics
also map unrepresentable or mixed-timezone timestamp arithmetic to controlled domain or redacted API
errors; raw Python datetime exceptions must not cross these boundaries.

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

`DatasetStore.list_verified()` serves full-history browser catalogue listings from the same deep
verification. Because datasets accumulate and execution consumers must always reread content,
listings reuse the previous verification result while every manifest and referenced Parquet file keeps
its recorded file type, byte size, modification time, change time, and content digest. An ordinary
in-place write or replacement invalidates that identity even if an actor restores the original size
and modification time, including when change time is unchanged in the same timestamp tick. Listings
capture this identity before and after deep verification and cache only an unchanged snapshot;
missing files, stat failures, or digest mismatches are cache misses. Deep execution loads always
reread and reverify content regardless of this listing cache.

`DatasetStore.list_latest_verified()` does not deep-verify that cumulative history. It cheaply parses
only the identity and time bounds needed to group manifest candidates, then deep-verifies the newest
candidate per provider/product/timeframe. If that candidate is corrupt, discovery continues
newest-first until it finds a valid prior revision. `GET /api/v1/market-data/datasets/latest` serves
this bounded catalog to the strategy launch form, while `/datasets` keeps returning every verified
revision so operators can inspect full history and stored results can resolve exact source
fingerprints. Shared cache access is serialized across concurrent catalog requests, and both
filesystem-backed routes run in FastAPI's worker thread pool so even a cold full catalog verification
cannot block unrelated API requests.

```text
<dataset-root>/
  coinbase/BTC-USD/1h/2026/07/01/part-<content-sha256>.parquet
  manifests/<content-sha256>.json
```

The manifest records schema version, provider, product, timeframe, requested range, expected and
received counts, gap/missing facts, completion outcome, fingerprint, and relative Parquet files.
The internal writer is deliberately not an API mutation endpoint. Confirmation-gated
`POST /api/v1/data/ingest` only sets `ingest_requested_at`. The separately supervised
`thytrader-market-data-worker` process is the only component that turns validated provider ranges into
durable datasets. It is distinct from `thytrader-worker`, which records portfolio valuation history.

## Worker lifecycle and durable diagnostics

The market-data worker aligns each cycle to the last complete bar of the watch timeframe. Its first
cycle requests a bounded lookback. Later cycles plan from durable verified coverage: forward
incremental (one-bar overlap), or prefix backfill when the watch starts before the island.
After restart, the worker first honors any persisted retry deadline and verifies
the current immutable dataset before trusting durable coverage. Later cycles inside the same covered
window update scheduling diagnostics without provider or dataset I/O when the island already covers
the watch. The overlap permits a delayed
upstream revision to replace the same canonical candle
deterministically while continuity is revalidated across the whole resulting range.

The worker claims each attempt in PostgreSQL before provider I/O. Operator-requested ingest skips
backoff and is claimed as soon as the worker sees `ingest_requested_at`. The atomic claim requires both a
newer attempt timestamp and the consecutive-failure count used to plan its backoff; a worker whose
snapshot lost that comparison stops before contacting the provider. It publishes only when the exact
requested increment and the cumulative result are both complete, contiguous, and gap-free. It reads
the new manifest and Parquet data back before atomically advancing PostgreSQL's authoritative
fingerprint and revision. A failed retrieval, merge, write, or read-back leaves the prior revision
authoritative.

Failures retain prior verified coverage while recording a stable redacted code/message, consecutive
failure count, and next retry instant. Dataset-verification failures retain those historical facts but
report coverage availability as unavailable until verification succeeds. Retries use capped
exponential delay with positive jitter, and persisted deadlines remain effective across restarts. State
reader boundaries reject malformed or non-UTC timestamp fields before any worker ordering,
retry scheduling, provider I/O, or API serialization. Readers also require worker identity, enum,
boolean, counter, failure, coverage, and content-fingerprint facts to form one coherent lifecycle
state. Complete coverage counts must equal the aligned coverage duration, coverage must retain its
success instant, and failed states must retain a later retry deadline; malformed PostgreSQL enum
values are translated to the same controlled state error. State transitions are monotonic: stale attempts, stale failure
snapshots, and late worker completions cannot
replace a newer attempt, double-count failures, shorten exponential backoff, or regress verified
coverage. A later verified success clears failure and retry state. PostgreSQL is
authoritative for coordination;
Parquet remains authoritative for immutable candle content. Compose supervises this process
independently with its own readiness marker, restart policy, and persistent dataset volume.
Demo ingestion is explicitly identified as provider `demo`; it never masquerades as Coinbase data.

Whenever the aligned hourly boundary advances, the worker publishes a distinct cumulative immutable
revision. Unchanged daily partitions are referenced by the new manifest rather than rewritten; only
days touched by the overlap or new candles receive new immutable Parquet files.
`DatasetStore.load_candles(fingerprint)` validates the canonical manifest, every referenced
Parquet file, complete coverage, and the SHA-256 fingerprint before returning exact typed candles.
Identical retries are idempotent. ThyTrader does
not automatically prune published manifests or Parquet partitions yet: safe retention requires a
dataset catalog and reference tracking so an operator cannot delete data that a future reproducible
backtest names by fingerprint. Until that contract exists, operators must size the dataset volume for
continued accumulation and treat manual deletion as destructive maintenance performed only while all
ThyTrader processes are stopped and after preserving any required fingerprints.

This worker has no portfolio-history, strategy, backtest, paper/live trading, broker, transfer,
withdrawal, leverage, derivatives, or optimization authority.

## Next increments

The diagnostics create a tested boundary to expand rather than a side path to maintain.

1. **Additional timeframes** — 5m research datasets and 15m/30m/6h/1d/1m/2h/4h complete-only
   datasets are implemented. Phase 7 data-loop hardening is also implemented. [ADR 0040](../decisions/0040-venue-strategy-paper-live-htf-clocks.md)
   later made every ingested venue TF a legal LTF, paper, live, discretionary, and HTF token.
   [ADR 0041](../decisions/0041-paper-live-htf-filter-evaluation.md) later evaluated `htf_filter` in
   paper and live.
2. **Additional ingestion targets** — an explicit watchlist plus confirmation-gated `thytrader-data` ingest cover extra USD spot products and every shipped dataset timeframe without weakening complete-only publication.
3. **Sub-hour live** — paper and live may evaluate closed venue bars. Sub-hour live pauses unless the
   authenticated user-order feed is connected ([ADR 0036](../decisions/0036-phase-13-live-extras.md),
   [ADR 0040](../decisions/0040-venue-strategy-paper-live-htf-clocks.md)).

Only a validated, immutable dataset with a fingerprint may become a Phase 3 backtest input.
