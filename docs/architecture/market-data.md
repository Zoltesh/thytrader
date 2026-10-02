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
binary completeness result. It is bounded to 129,600 candles (90 days at 1m). Watch lookbacks stay
within per-timeframe ceilings (`market_data.lookback`, [ADR 0085](../decisions/0085-fast-research-ingest.md)):
1m 90 days, 5m 1 year, 15m 2 years, 30m 3 years, 1h 5 years, and 2h/4h/6h/1d 10 years, each within
the 129,600-bar cap. Ranges cannot end in the future. An HTTP 429 from either Coinbase call raises
the provider-neutral `MarketDataRateLimitedError`.

The worker maintains immutable, fingerprint-addressed 1h, 5m, 15m, 30m, 6h, 1d, 1m, 2h, and 4h
historical datasets ([ADR 0085](../decisions/0085-fast-research-ingest.md)). Every provider request
is one interval-aligned page of at most 350 bars (`HISTORICAL_REQUEST_MAX_CANDLES`); a page that
extends a published island carries one overlap bar. Initial backfill starts at the newest closed
bar and walks back toward the lookback start, so coverage ends at the newest bar after one request;
when the watch lookback starts before `covered_starts_at` of a complete island, prefix backfill
(`prefix_backfill`) walks back the same way from the island start. Incremental maintenance walks
forward from a one-bar overlap. Each walk publishes one cumulative revision.

Coinbase returns no candle for an interval without trades
([ADR 0095](../decisions/0095-sparse-markets-no-trade-bars-listing-floors.md)). A missing bar older than the settle window (one bar, at
least 15 minutes) that the page request and its one confirmation re-fetch both omit is a confirmed
no-trade interval. It is published as a flat bar: `open = high = low = close` = the previous close,
`volume = 0`. The manifest counts these bars as `synthetic_no_trade_intervals` (written only when
non-zero, recomputed from the rows at verification, outside the content fingerprint, so gap-free
series stay byte-identical). No bar is invented before a market's first trade. When the lookback
start itself had no trades, the newest earlier trade prices the flat bars from the start. Forward
walks always extend the same island, starting from the stored edge bar when the provider omits a
no-trade overlap bar. A missing bar inside the settle window is waited for and never filled, so a
late candle cannot be replaced; a thin 1m market's head can trail closed time by up to 15 minutes.

`history_floor_at` (worker state column, Alembic 0051) comes only from a backward walk's listing
search. When a confirmed page adds no candle older than the segment, the walk pages to the UTC day
boundary, then probes daily candles (350 days per request, each confirmed) back to one daily page
past the timeframe's lookback ceiling, skipping whole days without trades. Only a search that finds no candle at all
records the floor, at the oldest real bar: the market had not traded yet. Forward walks never set or
move it. The search may spend up to 48 requests beyond the per-cycle budget, and the worker re-proves
a recorded floor once per process. Alembic 0059 cleared every floor recorded under the older
first-hole rule, which had pinned thin markets to their newest island (BONK-USD 1m kept two candles of
a 90-day watch). `watch_complete` treats a floor at the island start as the watch start, and counts
coverage that reaches the settle cutoff as spanning the watch.

For a watched target, `complete` on catalog, ingest status, gap inspection, and operator
data-catalog payloads is watch-relative: it is true only when the verified series spans the
configured half-open watch window (or starts at a proven listing floor), like `watch_complete`.
`island_complete` keeps the dataset-level fact. Coverage is `watch_covered_candle_count` of
`watch_expected_candle_count` bars. These payloads put `watch_complete` on the decision surface
before `complete`.
`GET /api/v1/market-data/datasets` and `/datasets/latest` list fingerprint-addressed island
publications; they are not a watch-completeness surface.
`inspect-gaps` classifies missing bars as `not_fetched`,
`exchange_unavailable`, or `incomplete_local`; it never interpolates, no-trade bars are published
bars rather than gaps, and a clean short island does not produce `gap_count: 0` for an incomplete
watch. Server-side time, probe, and row budgets can
stop the scan: the payload then sets `truncated` and a partial `gap_summary`
([ADR 0072](../decisions/0072-catalog-health-bounded-gaps-self-complete-ingest.md)).
`POST /api/v1/data/ingest` queues an ingest job (HTTP 202) for an existing watch and does not call
`ingest_once`. An unwatched product/timeframe is HTTP 409 naming `watch-add`; ingest never creates
a watch or picks a lookback. The market-data worker is the only publisher. The API Compose volume
stays `:ro`. Preview/range endpoints remain diagnostics, not strategy inputs. The worker keeps
`ingest_requested_at` until `watch_complete` or a durable failure. Every due target spends at most
a per-cycle request budget (24 with a pending request, otherwise 8). Covered watches are refreshed
first, then requested targets (oldest request first), then other backfill, and a target that ran out
of budget makes the next cycle start without the idle wait. One `ProviderPacer` spaces provider
calls by 0.25 s; an HTTP 429 sets a shared cooldown (2 s doubling to 60 s), keeps the pages already
fetched, and records `provider_rate_limited` only when a walk made no progress. The heartbeat is
touched before every request, so one ingest queue finishes its lookback without extra
`fill-gaps` calls.

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

`DatasetStore.load_verified()` accepts only a complete manifest at that canonical fingerprint path.
It validates identifier/time/count facts, resolved paths beneath the configured root, and complete
candle coverage after reading every referenced Parquet file; it then recomputes the fingerprint before
returning a dataset to a backtest or worker. A successful verification is cached per manifest and
served again only while the manifest and every Parquet file keep the exact stat identity and SHA-256
digest captured around it, so a hit never vouches for other bytes. `load_candles()` returns the
candles decoded during that verification (bounded in memory by `candle_cache_budget`; the worker
keeps none) instead of re-reading the files afterwards. This removed most of a backtest submit's
cost: one submit used to verify the same dataset about 14 times.

`DatasetStore.list_verified()` serves full-history browser catalogue listings from the same deep
verification. Because datasets accumulate and execution consumers must always reread content,
listings reuse the previous verification result while every manifest and referenced Parquet file keeps
its recorded file type, byte size, modification time, change time, and content digest. An ordinary
in-place write or replacement invalidates that identity even if an actor restores the original size
and modification time, including when change time is unchanged in the same timestamp tick. Listings
capture this identity before and after deep verification and cache only an unchanged snapshot;
missing files, stat failures, or digest mismatches are cache misses. Deep execution loads always
reread and reverify content regardless of this listing cache.

`DatasetStore.list_latest_verified()` is a catalog-grade listing ([ADR 0085](../decisions/0085-fast-research-ingest.md)).
It cheaply parses only the identity and time bounds needed to group manifest candidates, then checks
the newest candidate per provider/product/timeframe structurally: manifest facts and canonical
content address, safe in-market partition paths without duplicates, every file present, and an
intact Parquet envelope (magic at both ends). It does not decode rows or recompute fingerprints;
every path that binds a dataset to a run resolves the exact fingerprint through full verification,
so a damaged listed revision fails closed there. Entries are cached by the stat identity of the
manifest and every file, and partition paths and envelopes are cached per file, so a new cumulative
revision checks only its new partitions. If the newest candidate fails, discovery continues
newest-first until a prior revision passes. On a production-shaped catalog (87 markets, about 17,000
partitions) a warm listing takes about 0.2 s and stays under a second while every market publishes
a new revision each cycle. `GET /api/v1/market-data/datasets/latest` and operator `data-catalog`
serve this catalog, while `/datasets` keeps returning every deep-verified
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

## Superseded-revision retention

Every ingest walk publishes a new cumulative revision, and `extend` reuses unchanged day
partitions, so superseded manifests accumulate (40,704 manifests / 1.1 GB on 2026-10-01). The
market-data worker, which is the only writer of the dataset volume, runs a bounded retention pass
at startup and every 6 hours (`thytrader.market_data.dataset_retention`). A truncated pass runs
again on the next cycle. A manifest is deleted only when all of these hold:

- No persisted record references it. Every text and JSON column in the schema is scanned in SQL
  for `sha256:<64 hex>` tokens in one read-only snapshot: strategy dataset bindings, run specs,
  backtest results, study and job payloads, worker state, audit details, and any future table.
- It is not maximal for its provider/product/timeframe: another manifest covers its whole range
  (wider, or the same range published later). The newest revision and the final revision of every
  older island are always kept.
- The earliest covering manifest was published at least 24 hours ago. A reader that resolved it as
  latest just before supersession has time to bind it.
- The covering maximal revision passes full verification (files present, hashes match).

Manifests are unpublished first. A Parquet file is removed only when no surviving manifest lists
it, and empty day directories are pruned. An unreadable manifest, a failed reference scan, or a
concurrent pass (advisory lock `.retention.lock` in the dataset root) aborts the pass with nothing
deleted. Each pass logs `market_data_dataset_retention` with counts. Passes that delete or abort
append a `market_data` / `dataset_retention` audit event. No candles are rewritten or interpolated.

The worker clears the existing backlog by itself, at most 5,000 manifests per pass. For an
immediate one-shot repair, run `docker compose exec market-data-worker
/app/.venv/bin/thytrader-market-data-retention` (a dry run that prints a JSON report), then add
`--confirm` to delete. `--max-manifests` (default 50,000) and `--grace-hours` (default 24) bound
the run.

## Worker lifecycle and durable diagnostics

The market-data worker aligns each cycle to the last complete bar of the watch timeframe. Its first
cycle requests a bounded lookback. Later cycles plan from durable verified coverage: forward
incremental (one-bar overlap), or prefix backfill when the watch starts before the island and no
`history_floor_at` sits at the island start. Incomplete-page warnings (`code=chunk_incomplete`)
carry `product_id`, `timeframe`, `direction` (`initial` / `prefix` / `forward` / `listing_probe`),
`starts_at`, `ends_at`, `expected`, `received`, and `missing_intervals`; on thin markets they are
routine. `market_data_no_trade_bars_published` logs each publication that carries no-trade bars,
`market_data_history_floor_recorded` logs each proven listing floor, `market_data_ingestion_walk`
logs each walk's stop reason (`complete`, `budget`, `unsettled`, `listing_floor`, `inconsistent`,
`rate_limited`, ...) and request count, and `market_data_ingestion_rate_limited` logs each throttle
with its cooldown.
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
