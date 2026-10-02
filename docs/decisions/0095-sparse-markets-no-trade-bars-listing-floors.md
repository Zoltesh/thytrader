# 0095: Sparse markets keep their history: confirmed no-trade bars and listing-only floors

- Status: Accepted
- Date: 2026-10-02
- Supersedes in part: [0085](0085-fast-research-ingest.md) ("missing bars end a run and are never
  interpolated", "a final hole directly before the walked segment becomes `history_floor_at`", and
  "a final hole in a forward walk starts a newer island, so the newest contiguous island wins")
- Amends: [0087](0087-per-bar-decision-timeline.md) (decision records name no-trade bars) and
  [0083](0083-unified-backtest-model.md) (results disclose evaluated no-trade bars)
- Relates to: [0016](0016-longer-complete-5m-datasets.md), [0038](0038-complete-only-1m-2h-4h-datasets.md),
  [0068](0068-slow-timeframe-watch-lookback-and-catalog-ingest.md),
  [0072](0072-catalog-health-bounded-gaps-self-complete-ingest.md); ops contract v55 / Alembic 0059

## Context

Coinbase returns no candle for an interval in which nothing traded. The ADR 0085 worker treated
every confirmed missing bar as a provider hole. On a thin market that broke three things at once:

1. **The dataset shrank to its newest island.** A forward walk that met a quiet minute started a
   detached island after it and republished only that island. BONK-USD had a 90-day 1m watch
   (`lookback_hours` 2160); its catalog dataset covered 2026-10-02 10:57Z to 10:59Z (two candles), and
   its 5m dataset about eleven hours.
2. **The floor moved forward and blocked backfill.** The island start was recorded as
   `history_floor_at`, which prefix backfill treats as "Coinbase has nothing older". Each quiet
   interval moved it again (10:51, then 10:57), so the series could never grow back.
3. **The catalog called it complete.** The two-candle island was gap-free, and the floor at its start
   made `island_covers_watch` count the watch as satisfied: `complete: true`, `sparsity: none`,
   `watch_status: complete`.

The worker log showed the sequence: `chunk_incomplete ... direction=forward expected=21 received=12
missing_intervals=7`, then `history_floor_recorded ... 10:57`, then `kind=incremental stop=hole`.

## Decision

**Interior no-trade intervals become flat bars (option a).** A bar is a confirmed no-trade
interval when it is older than the settle window (one bar, at least 15 minutes) and both the page
request and its one confirmation re-fetch omit it (a bar present in either response is real). Each
becomes one flat bar: `open = high = low = close` = the previous close, `volume = 0`.

- A bar is never synthesized before the first real candle: without a previous close there is no
  honest price. When the lookback start itself had no trades, the newest trade before it (found by
  the same search below the start, usually one page) prices the flat bars from the start. Bars below
  the start are not published.
- Real Coinbase candles always carry positive volume, so `volume == 0` identifies a no-trade bar. No
  Parquet column changes.
- Dataset manifests record `synthetic_no_trade_intervals` only when it is non-zero. Gap-free datasets
  therefore keep their exact manifest bytes and content fingerprints. The count is not part of the
  fingerprint. Deep verification recomputes it from the rows and fails when a stored count disagrees.
  Manifests written before this ADR take their count from the rows.
- Forward walks extend the island across confirmed no-trade bars and never start a detached island.
  When the provider omits the overlap bar (a stored no-trade bar), the walk starts the extension from
  the stored edge bar (`DatasetStore.load_edge_candle`). Prefix backfill does the same with the
  island's first bar. A missing bar inside the settle window still stops the walk until a later cycle
  (`IngestStop.UNSETTLED`), so a late candle is never replaced.

**A history floor comes only from a listing search in a backward walk.** Forward and incremental
walks never record or move `history_floor_at`, and an interior gap never becomes one.

- When a confirmed backward page adds no candle older than the segment, the walk searches below it.
  It fetches pages to the UTC day boundary, then confirmed daily-candle probes (Coinbase `ONE_DAY`,
  350 days per request) back to the **listing horizon**: one daily page (350 days) past the
  timeframe's lookback ceiling (1m 90 days up to 10 years). Whole days without a daily candle are
  skipped. The margin past the ceiling lets a watch at the ceiling price a quiet first bar from the
  trade before it, instead of mistaking that bar for a listing.
- If the search finds a trading day, the walk jumps to its end and resumes ordinary pages. The skipped
  days become flat bars at that day's last close.
- Only a search that reaches the horizon without any candle records the floor, at the oldest real bar
  (`IngestStop.LISTING_FLOOR`): the market had not traded yet. A floor proven back to the horizon holds
  for every lookback that timeframe allows.
- A search publishes nothing until it finds a candle, so it may spend up to 48 requests beyond the
  per-cycle budget (`LISTING_SEARCH_REQUEST_ALLOWANCE`). Without that allowance it would repeat every
  cycle. Its cost is bounded by the horizon.
- A confirmed page whose bounds, grid, or ordering disagree with its request stops the walk
  (`IngestStop.INCONSISTENT`, recorded as `incomplete_range`). Nothing is concluded from it.

**Floors are re-proven once per worker process.** On each target's first visit after a worker start,
planning ignores a recorded floor. A genuine listing floor is then proven again (a few requests), and a
floor over real history is replaced by that history. This also covers a floor that an older worker
wrote while a deploy was in progress.

**Repair: Alembic `0059` clears every recorded floor.** Worker state never recorded which walk wrote a
floor. Every pre-ADR 0095 floor came from the hole rule, so all of them are cleared. Affected series
prefix-backfill again on their next cycles. The migration also restates the column comment. Downgrade
restores only the comment.

**Catalog completeness is relative to the watch lookback.**

- For a watched target, `complete` on the operator `data-catalog` row and on the data-lane ingest and
  gaps payloads now means `watch_complete`: the verified series spans the lookback, or starts at a
  proven listing floor. A two-minute dataset for a 90-day watch is not complete.
- `island_complete` keeps the dataset-level fact.
- Coverage is reported as `watch_covered_candle_count` of `watch_expected_candle_count` bars, with
  `watch_coverage_ratio` and the manifest's `synthetic_no_trade_intervals`. Data health shows
  "X / Y · N no-trade" and "Complete from listing".
- `island_covers_watch` also counts coverage that reaches the settle cutoff as spanning the watch. A
  missing bar inside the settle window may still be published, so a sparse market's quiet head is not
  history the watch lacks.

**Research discloses no-trade bars.** A backtest whose evaluation window contains a no-trade bar adds
the validity limit `synthetic_no_trade_bars`. Gap-free windows keep their exact limits and result
fingerprints. The backtest model description gains the `no_trade_bars` assumption. The indicator
library already returns "undefined" for zero volume (VWAP, MFI, CMF) and for flat ranges (Stochastic,
Williams %R, CCI, %B, Choppiness), so such conditions evaluate `unknown`, never crash.

**Paper and live evaluate the same bars.**

- The closed-bar window loader re-fetches once when the provider leaves a bar missing between two traded
  bars. It fills the bars both responses omit exactly as datasets do, so a strategy sees the series its
  backtest saw.
- A missing newest closed bar is never filled: the window stays gapped and the book pauses with
  `skipped` / `data_gap`, as before.
- No-trade bars are evaluated like any bar. Their decision record carries `no_trade_bar: true`, and its
  summary ends "(no-trade bar: no trades, flat at the prior close)". Records written before this ADR
  read as `false`.

**Contract.** Ops contract `thytrader-ops-contract-v55` expects Alembic `0059` and adds the
`catalog_health` tokens `no_trade_bars`, `listing_history_floor`, and `watch_relative_complete`.

## Consequences

- A quiet interval never shrinks a series. BONK-USD 1m backfills its 90 days after the repair, with its
  quiet minutes as flat bars.
- Liquid gap-free series are byte-identical: the same Parquet rows, manifests, and content fingerprints
  (pinned by `test_liquid_gap_free_series_keep_their_exact_fingerprints`).
- Proving a listing costs more requests than recording the first hole did. A five-year 1h watch on a
  one-year-old product needs 43 requests instead of 27, once per product and once per worker process.
  A 90-day 1m listing proof is two daily probes, each confirmed once.
- Sparse series carry synthetic bars. Their research results disclose `synthetic_no_trade_bars`, and
  their volume-based indicators read undefined over quiet stretches.
- A sparse 1m head can lag closed time by up to the 15-minute settle window. Freshness may then read
  `stale` while `watch_complete` holds.
- Deferred:
  - A quiet newest bar still pauses a paper or live book (`data_gap`). A non-pausing wait for a
    settling newest bar would change risk policy and needs its own decision.
  - Older revisions superseded by the old island rule are not reconstructed; the backfill re-fetches
    them.
  - The daily probe trusts Coinbase daily candles to agree with finer granularities. A disagreement only
    makes the walk page one more day.

## Alternatives considered

- **(b) Keep gaps and make every consumer handle them.** Indicators, the backtest kernel, signal
  traces, and paper/live evaluation all assume contiguous bars, and the dataset store verifies
  completeness. Option (b) would change all of them and their fingerprints, and paper/live would still
  differ from research. Rejected.
- **Fill at read time.** This stores gaps and fills them in `load_candles`, which leaves two
  representations of one dataset and a fingerprint that no longer describes the evaluated bars.
  Rejected.
- **A per-row synthetic flag column.** This changes the Parquet schema and therefore every
  fingerprint. `volume == 0` already identifies no-trade bars. Rejected.
- **Floor after one empty page.** A thin 1m market with a quiet stretch longer than 350 minutes would
  get a false floor, which is the bug again with a larger threshold. Rejected.
- **Lookback-relative floors, or a persisted probe cursor.** Raising a lookback would need a new proof,
  and an empty run longer than one cycle's budget would repeat forever. A cursor needs a schema change.
  Daily probes back to the horizon bound the search instead. Rejected.
- **Clear only floors written by forward walks.** Worker state never recorded the writing walk, and
  backward walks also wrote floors at interior gaps. Rejected in favor of clearing all floors and
  re-proving.
