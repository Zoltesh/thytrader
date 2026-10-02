# 0085: Fast research ingest — ranged backfill, research lookback ceilings, explicit watched ingest, catalog-grade listings

- Status: Accepted. Superseded in part by [0095](0095-sparse-markets-no-trade-bars-listing-floors.md):
  confirmed no-trade intervals are now flat bars, forward walks never start a newer island, and
  `history_floor_at` comes only from a backward listing search.
- Date: 2026-10-02
- Supersedes in part: [0068](0068-slow-timeframe-watch-lookback-and-catalog-ingest.md) (the
  2,160-hour and 8,760-hour watch lookback ceilings) and
  [0072](0072-catalog-health-bounded-gaps-self-complete-ingest.md) (the "small UTC-day budget per
  target per cycle" ingest walk)
- Relates to: [0016](0016-longer-complete-5m-datasets.md), [0038](0038-complete-only-1m-2h-4h-datasets.md),
  [0019](0019-ops-contract-identity.md); ops contract v45 / Alembic 0052

## Context

Research across many Coinbase USDC markets was bottlenecked on market-data ingest:

1. **One UTC day per request.** The worker fetched one UTC-day chunk per provider call, at most two
   chunks per target per cycle. Coinbase serves up to 350 candles per request, so a year of 1d bars
   cost about 365 requests instead of 2, and a year of 1h bars about 365 instead of 26. Each chunk
   also published a cumulative revision. Every publication re-verified the whole growing island
   (about 2.7 s for one year of 1h and more than 15 s for 90 days of 1m), so backfill cost grew
   quadratically. Observed throughput was about six candle-days per minute, shared by all targets.
2. **Tight ceilings.** Watch lookbacks stopped at 90 days for 1m to 1h and one year for 2h to 1d.
   The technical cap is 129,600 bars per range.
3. **Silent default.** `thytrader-data ingest` on an unwatched target silently created an enabled
   watch with the worker default of 168 hours.
4. **Slow catalog reads.** `datasets/latest` and `data-catalog` deep-verified the newest revision of
   every market. They decoded every Parquet row and recomputed every content fingerprint, and warm
   reads re-hashed every Parquet file. Because backfill publishes a new revision for most markets
   every cycle, most reads ran the cold path: tens of seconds to minutes.
5. **Repeated dataset verification on backtest submission.** One synchronous submit fully verified
   the same dataset about 14 times (strategy binding checks, run-artifact checks, the evaluation
   window, and the candle load), which was about 80% of a 10 s submit.

## Decision

**Ranged, newest-first, budgeted ingest.**

- Every provider request is one interval-aligned page of at most `HISTORICAL_REQUEST_MAX_CANDLES`
  (350) bars. A page that extends a published island includes one overlap bar.
- Initial backfill starts at the newest closed bar and walks back toward the lookback start. Coverage
  ends at the newest bar after the first request. A product listed after the lookback start stops the
  walk at its listing hole (`history_floor_at`). The old oldest-first walk instead re-probed empty
  pre-listing days and, under its per-cycle budget, never reached the data. Prefix backfill uses the
  same walk from the island start. Incremental maintenance walks forward from a one-bar overlap.
- Pages are split into gap-free runs. Missing bars end a run and are never interpolated. A run is
  published only if `analyze_range` and `DatasetStore.write`/`extend` re-verify it as complete.
  Fingerprints and UTC-day Parquet partitions are unchanged.
- Holes keep the [ADR 0068](0068-slow-timeframe-watch-lookback-and-catalog-ingest.md) floor
  semantics at bar granularity. A missing bar older than the settle window (one bar, at least 15
  minutes) is final after one confirmation re-fetch.
  - A final hole directly before the walked segment becomes `history_floor_at`.
  - A final hole in a forward walk starts a newer island, so the newest contiguous island wins.
  - A missing bar inside the settle window is waited for, never recorded, so a late Coinbase candle
    cannot discard a long island.
- Each walk publishes one cumulative revision, not one per page or day.
- Every due target gets a per-cycle request budget: 8 requests, or 24 with a pending
  `ingest_requested_at`. Order within a cycle is covered watches first (cheap upkeep), then requested
  targets (oldest request first), then other backfill. When any target runs out of budget with work
  left, the next cycle starts without the idle wait.
- One `ProviderPacer` spaces every provider call by 0.25 s, so the worker stays near eight Coinbase
  HTTP calls per second and leaves headroom on the shared key. The Coinbase adapter maps an HTTP 429
  to the provider-neutral `MarketDataRateLimitedError`. A throttle stops the target's walk after
  publishing what it fetched, and sets a shared cooldown (2 s, doubling to 60 s). Without progress it
  records the redacted `provider_rate_limited` failure with a short retry.

**Research lookback ceilings** (`market_data.lookback.WATCH_LOOKBACK_MAX_HOURS`), each within
129,600 bars:

| Timeframe | Ceiling (hours) | Span | Bars |
|---|---|---|---|
| `1m` | 2,160 | 90 days | 129,600 |
| `5m` | 8,760 | 1 year | 105,120 |
| `15m` | 17,520 | 2 years | 70,080 |
| `30m` | 26,280 | 3 years | 52,560 |
| `1h` | 43,800 | 5 years | 43,800 |
| `2h`, `4h`, `6h`, `1d` | 87,600 | 10 years | 43,800 / 21,900 / 14,600 / 3,650 |

PostgreSQL keeps one widest CHECK bound (87,600 hours, Alembic 0052). The API and domain enforce
the per-timeframe ceiling with a message naming both the number and the span. The CLI help, the
in-app chat tool, and the web Download data… dialog list the same table. Coinbase availability is
left to the history floor. The worker-default setting `market_data_worker_lookback_hours` still only
seeds the 1h default watch and keeps its 2,160-hour bound.

**Ingest is explicit for unwatched targets: it refuses.** `POST /api/v1/data/ingest` and
`/fill-gaps` (and therefore `thytrader-data ingest`, `fill-gaps`, and the chat tools) answer
**HTTP 409** for a product/timeframe without a watchlist row. The message names
`thytrader-data watch-add --product-id … --timeframe … --lookback-hours <1-N> --confirm`. Ingest
never creates a watch and never picks a lookback. 409 rather than 404, because agent CLIs read a
404 on a data route as the stale-image signal.

**Catalog-grade listings; exact verification at bind time.** `DatasetStore.list_latest_verified()`
(behind `GET /api/v1/market-data/datasets/latest` and operator `data-catalog`) verifies each newest
revision structurally:

- manifest schema and facts, and its canonical content-addressed path
- safe, in-market partition paths, with no duplicates
- every file present as a regular file
- an intact Parquet envelope (magic at both ends)

It no longer decodes rows or recomputes fingerprints. Entries are cached by exact stat identity
(device, inode, size, mtime, ctime) of the manifest and every file. Partition-path validation and
envelope checks are cached per file, so a new cumulative revision checks only its new partitions.
`list_verified()` (`GET /datasets`) keeps full deep verification.

**Verified-dataset cache.** `DatasetStore.load_verified()`, `load_manifest()`, and `load_candles()`
cache a successful full verification per manifest. A hit is served only while the manifest and every
Parquet file keep the exact stat identity **and SHA-256 digest** captured around that verification.
Any difference runs the full verification again, so a hit never vouches for bytes other than the
bytes that were verified. `load_candles()` returns the candles decoded during verification instead of
re-reading the files afterwards (that re-read was a time-of-check gap). Decoded candles are cached
within a budget (120,000 by default). The ingest worker keeps only manifests (budget 0).

**Agent transport timeouts are explicit.** `agent_http.request_json` maps a client-side read timeout
(the server accepted the request but did not answer) and a connect timeout to an
`AgentHttpError(timed_out=True)`. The message names the call and says whether the request may still
be running. These timeouts used to escape as bare `TimeoutError` and print each CLI's generic
"failed safely" line. `submit-backtest` adds a `list-results` / `--async` hint. Study submits keep
their strategy-scoped readback.

## Consequences

- Request counts, measured with a Coinbase-shaped fake provider (tests in
  `tests/market_data_worker/test_ranged_ingest.py`):
  - one year of 1h: 26 requests instead of 365
  - one year of 1d: 2 instead of 365
  - a five-year 1h watch on a one-year-old product: 27 requests. The previous walk never finished
    this case: with its two-UTC-day budget it re-probed the same two empty pre-listing days every
    cycle (20,000 requests over 5,000 cycles, nothing published). Any watch reaching back before a
    product's listing was stuck the same way.
- Each walk is one publication. A 1-year 1h backfill now spends about 13 s in requests at the paced
  rate, plus two publications.
- `datasets/latest` and `data-catalog` stay under one second warm, including while every market
  publishes a new revision each cycle. Listings can show a revision whose Parquet bytes were later
  corrupted in place. Such a revision fails closed when a run binds it, because binding resolves the
  exact fingerprint through full verification.
- The cache holds candle memory up to the budget (about 80 MB at 120,000 candles). Cache hits still
  hash every file (tens of milliseconds for a year of daily partitions).
- Older complete runs before a hole are not published as separate, disjoint islands any more. The
  island after the newest hole is the dataset, and `history_floor_at` marks its start. The previous
  day-chunk walk left such runs on disk only as a side effect.
- Ops contract v45 adds the `catalog_health` tokens `ranged_backfill`, `explicit_watch_ingest`, and
  `research_lookback_ceilings`, and expects Alembic `0052`. CLIs against an older image fail closed
  and ask for `make run`.
- Remaining backtest-submit cost is outside the dataset store and is left for its owners:
  result canonicalization is repeated about six times, and the signal trace is evaluated twice.

## Alternatives considered

- **Bigger day chunks, oldest-first.** This keeps one publication per chunk and still spends two
  requests per empty pre-listing day under ten-year ceilings. Rejected.
- **Ingest accepts `--lookback-hours` and creates the watch.** This makes ingest a second way to
  configure watches, and is ambiguous when a watch already exists with another lookback. Rejected in
  favor of one explicit `watch-add` lane.
- **A latest-per-market index file written at publish.** This adds a second source of truth that
  retention and manual repair must keep in sync. The stat-identity cache gives the same warm latency
  with no new on-disk state.
- **Serving catalog rows from worker state alone.** This lists coverage the volume may no longer
  hold. Rejected.
- **Dropping the read-back verification after publication, or skipping verification on hits by stat
  alone.** Stat identity cannot detect a same-size in-place rewrite within one timestamp tick, so
  every hit re-hashes the files. Rejected.
