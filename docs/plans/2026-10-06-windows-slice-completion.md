# Windows slice completion — 2026-10-06 (ADR 0113)

## Scope and delivered behavior

Worktree `/home/hermes/projects/tt-review-windows`, branch `feat/review-windows`, original
HEAD `901a059`. Implementation stays in the owned `_closed_*` loader regions, market-data
service/cache/demo modules, their tests, ADR 0113, and narrow runtime/data skill additions.
No production API/CLI calls, service changes, credential/dotenv reads, ingestion, operational
PostgreSQL access, migration, or running bot/policy changes were performed. Commit is local;
lead alone integrates/pushes/merges/deploys.

- Fixed deploy start retained beyond 129,600 intervals. Settled prefix and empty-scan progress
  use 349-bar segments + one overlap, at most eight top-level range calls per window load.
  Coinbase candle paging is bounded to one page per such call; metadata/preview HTTP calls
  and the aggregate across all loads in a worker cycle are not covered by that budget.
- Mutable publication tail re-fetched every same-end/incremental load. Bars freeze only after
  close + 120s; the newest requested bar always remains mutable. Corrections and disappearing
  head candles remain visible. Frozen overlap cannot revise canonical seeds.
- Sparse settled blocks require a second observation. Leading/newest/unsettled holes never
  acquire invented prices; interior confirmed no-trade bars have exact prior close/zero
  volume. Empty leading/intermediate blocks advance; pending confirmations survive budgets.
- Earlier as-of requests cannot fetch future candles or use later pending-block evidence.
  A historical cap ending inside a cached no-trade gap withholds the synthetic head until
  following real evidence is visible inside that cap.
- Shared HTF/extra windows cover union warmup and are reused only with sufficient fixed-start,
  contiguous, latest/as-of coverage. The evaluator still selects each consumer's exact bars.
- References use `closed_bar_required_coverage`, including offsets, previous crossover values,
  and signal exits. Missing references stay entry-only gates, not bot pauses.
- Demo candles now have UTC-time identity, not range-relative price identity. Whole ranges,
  segments, previews, overlap and restart agree. Demo data remains explicitly synthetic;
  historical demo publications/fills are not rewritten.

## Honest bug verification

The initial suspected shared-HTF underdeclared warmup is already prevented by strict strategy
validation (including operand lags). Tests explicitly pin that rejection. The defensive loader
repair concerns insufficient supplied/reused windows, not a newly accepted strategy shape.

The legacy reference `warmup + 1` already covers all tested legal rollovers/same-clock starts.
The demonstrated difference is an unnecessary extra earlier bar mid-bucket. New and legacy
reference values, lagged crossover previous/current reads and signal-exit results agree.
There is no claim that a missing reference rollover candle was reproduced.

The real lifetime defect and mutable-tail draft defect are reproduced with hermetic fakes.
Segmentation also exposed a real demo defect: the same timestamp previously changed OHLCV
when requested from another range start. The demo fix is essential to cache correctness.

## Interfaces and lifecycle integration

New modules:

- `market_data/window_cache.py`: `DeployWindowCache.closed_window(fetch_range, *, product_id,
  interval, starts_at, last_closed_start, now) -> tuple[Candle, ...]`; internal frozen prefix,
  scan and pending-confirmation state; LRU candle-count budget and serialized mutation.
- `market_data/window_state.py`: shared `WindowCacheWarmingError(RuntimeError)` with precisely
  typed `product_id`, `interval`, `starts_at`, `scanned_through`, `requested_end`,
  `range_requests`. Constructor matches the lifecycle lane byte-for-byte. Windows includes
  the identical new file so its commit/tests stand alone; merge identical add/add rather
  than creating a second exception class. It is also importable via `window_cache`.

`MarketDataService` gains `window_cache`, with optional keyword injection in its constructor;
normal service replacement starts a fresh provider-specific cache. `_closed_window_for`, HTF,
and extra-clock loaders propagate warming instead of returning empty/None for local unfinished
work. Best-effort reference warming supplies an empty reference only, without unavailable-feed
warning. No HTTP/CLI payload, flag, strategy schema or database shape changes.

Lifecycle coordination: `/tmp/tt-implementation/agents/windows-interface-note.md` and the
lifecycle lane's reply establish that `WindowCacheWarmingError` is caught at decision,
HTF/extra and discretionary paths; reconciliation/protection continue without candle-based
entries. Do not advance the cursor, save PAUSED, auto-resume operator pauses or reset breakers
on warming. Windows intentionally does not edit `_process_stopped`, `_advance_strategy` or
`_run_cycle`. **Integrated release needs the lifecycle lane** for no-candle supervision;
standalone baseline's broad cycle error handler only prevents accidental lifecycle writes.

## Limits, not hidden promises

This is **process-local exact anchored-prefix caching**, not persistent O(1) indicator state.
Full returned prefix, indicator arrays and evaluator CPU/memory grow with lifetime. LRU counts
two million retained candles by default, including pending blocks; a single larger active
window is exempt to avoid endless rebuilding. Byte/peak memory is not measured by that count.

Restart/eviction rebuilds at the same fixed start. With unchanged settled upstream data it is
candle-for-candle and EMA-for-EMA identical. Within a generation settled revisions are ignored;
a new generation re-observes upstream revisions. Fixed seed boundary is guaranteed, immutable
venue content across restart is not (no frozen execution dataset fingerprint was bound).
Cold 90-day 1m rebuilding needs about 373 candle pages (372 prefix plus one head) before
warmup/confirmations, spread across many loads. Repeated eviction/restarts repay it. An
aggregate working set larger than the LRU budget can thrash/repeatedly rebuild; this is not
a guarantee of timely warming under arbitrary fleet/memory load. No account-wide rate budget
is claimed.

## Verification

All pytest commands explicitly unset `THYTRADER_TEST_DATABASE_URL` and
`THYTRADER_INTEGRATION_DATABASE_URL`; autouse fixture guards prevent dotenv/real-network use.
Own Python 3.14.4 `.venv` was verified (`sys.prefix` points to this worktree).

- `uv run pytest tests/market_data tests/execution_worker tests/execution tests/research -q`
  (with the two test DB variables unset): **1255 passed, 2 PostgreSQL-dependent skipped**,
  after the final as-of synthetic-head regression.
- Final window/worker integration run: `uv run pytest
  tests/execution_worker/test_window_longevity.py tests/market_data/test_window_cache.py
  tests/market_data/test_demo_window_identity.py -q` (same unset variables): **40 passed**,
  including the actual `_closed_window_for`/service/cache adapter-bound reproduction.
- `uv run pytest -q` (same unset variables): **2804 passed, 89 skipped, 2 warnings** on the
  final code, including the actual >90-day worker-loader test and as-of synthetic-head
  regression (144.14s). PostgreSQL integrations were not run.
- `uv run ruff check .`: pass.
- `uv run ruff format --check .`: pass.
- `uv run ty check`: pass, no new suppressions.
- `git diff --check`: pass.

Initial red regressions (mutable-tail/cache assumptions, validator-rejected fixture, demo
page identity and broad discretionary-price compatibility) were fixed and all above commands
rerun. Two early targeted commands named nonexistent test paths and ran no tests; corrected
commands use real suite directories.

## GitNexus and structural evidence

Copied local graph metadata is foreign; read-only `context`, `query`, and upstream `impact`
used `/home/hermes/projects/thytrader` as authorized. Root index was not mutated. Source and
executed tests resolve the sibling-HEAD freshness warning. `_closed_window_for` and
`MarketDataService` report MEDIUM risk; HTF/reference/extra loaders and demo candle helper LOW.
Callers include strategy, stopped, discretionary and lockstep evaluation; no indexed process
is evidence of absence. Demo provider method impact is UNKNOWN (dynamic Protocol dispatch);
source search confirms service and demo execution/ingest callers.

Local rebuild uses isolated `/tmp/tt-windows-owned-graph-20261006` storage via
`GITNEXUS_STORAGE_PATH`, alias `tt-review-windows`, one parse worker/one embedding thread,
`--index-only --pdg`. Copied worktree/root graph storage is untouched. Embedding attempts are
not claimed clean: default safety cap skipped 82,799 nodes; explicit 100,000 cap then failed
with duplicate `CodeEmbedding` primary key `:0`. A forced drop-embeddings retry stopped without
a success marker and left incomplete metadata. The final default-cap rebuild **succeeded**
(44.5s), repairing metadata and publishing 82,903 nodes / 187,254 edges / 801 flows with PDG;
embeddings were explicitly skipped at the safety cap. A subsequent fresh-code incremental
rebuild also succeeded (30.3s), publishing 82,929 nodes / 187,349 edges / 801 flows, again
with the 50k embedding safety-cap skip. Do not claim a full embedding rebuild.

Final local `status`: up-to-date, matching all 1065 covered files at the time of analysis.
Final staged `detect-changes --scope all --limit 1000`: **14 files, 99 changed symbols**
(136 on the earlier full-rebuild index), reported LOW risk, zero affected indexed processes;
CLI abbreviates its display after 15 symbols. Counts differ with graph refresh; neither
zero processes nor the symbol count is proof of complete dynamic caller coverage.
This is not an all-clear from absent flows: process traversal is known truncated. The final
cache class impact reports MEDIUM risk with 42 upstream symbols (direct service import and
API/worker consumers). Whole-symbol PDG upstream impact for `_closed_window_for` reports
UNKNOWN: its intraprocedural slice stays within the seeded body, while the symbol bridge
lists 15 callers. That UNKNOWN is unresolved graph evidence, not low risk; loader source,
caller inspection and the full hermetic suites are the authoritative verification. Lead
must repeat integrated graph checks and can use statement-specific PDG slices.

Local structural `check --cycles --json` enumerates two existing cycles (worker service/venue
TYPE_CHECKING imports and web deployment-detail/strategy-workspace); neither is introduced
by cache imports. Analyzer also warns of baseline process-traversal truncation and dynamic
candidate caps; a zero affected-process count cannot establish no regression risk.

## Shared files and release landing work

No edits to `OPS_CONTRACT_ID`, `EXPECTED_SCHEMA_REVISION`, global operator schema JSON,
ADR index, architecture index, roadmap, landing docs, migrations or Compose. Narrow shared
changes are only service imports/owned loader regions and appended sections in data/runtime
skills. ADR is `0113-deploy-anchored-window-cache.md`. Lead integrates architecture/user/agent
landing docs and shared release contracts as already assigned. Likely conflict is service.py
imports with lifecycle; preserve both lanes and the shared warming exception identity.
