# 0113: Fixed-seed execution windows with segmented prefix caching

- Status: Accepted
- Date: 2026-10-06
- Amends: [0095](0095-sparse-markets-no-trade-bars-listing-floors.md) (runtime publication-tail handling)
- Relates to: [0008](0008-deterministic-signal-evaluation.md),
  [0042](0042-per-indicator-timeframes.md), [0093](0093-signal-based-exits.md),
  [0096](0096-reference-instruments.md), [0099](0099-operand-level-indicator-offsets.md),
  [0104](0104-bounded-newest-candle-wait.md)

## Context and verified findings

`_closed_window_for` requested the entire warmup-to-current lifetime every cycle. Coinbase's
129,600-interval bound consequently fails a long-running `1m` book after roughly 90 days,
including warmup. Simply sliding the start changes the decision-clock EMA/ATR and other
recursive indicator seeds. Trading must retain the fixed deploy-prefix semantics.

Two suspected warmup defects need qualification, not invented reproductions:

- Snapshot validation already requires declared HTF warmup to cover extra indicators sharing
  that clock, including declaration/operand lags. A declared 50-bar HTF window with an 80-bar
  shared extra indicator is rejected. Nevertheless, the loader must defensively avoid reusing
  an undersized, gapped, stale, or future-capped shared-clock input.
- Reference `warmup + 1` already covers the previous mapped bar at legal clock rollovers.
  The demonstrated difference is **one unnecessary earlier bar mid-bucket**, not a missing
  rollover bar. Using `closed_bar_required_coverage` explicitly includes previous/current
  mappings, offsets, crossover and signal-exit reads without blanket padding.

Segmentation exposed a separate demo-only identity error: demo prices depended on the offset
inside the requested range. Identical candle timestamps therefore had different OHLCV when
requested through overlapping or segmented ranges. This violates a cache's provider contract.

## Decision

### Exact prefix, fresh tail

Each `MarketDataService` owns a process-local `DeployWindowCache`, keyed by product, interval
and exact fixed warmup start. There is no rolling start and no incremental-indicator rewrite.
Decision-clock evaluators still receive the entire anchored prefix. Extra/HTF/reference
consumers retain their existing exact mapped-bar selection before indicator calculation:
fetching a union does not expand a consumer's seed window.

Cache settled bars only when their exclusive close is at least 120 seconds before the UTC
observation, and always leave the newest requested bar outside the frozen prefix. Re-fetch
the entire publication tail on every load, with one overlap. A correction to a recent closed
bar is observed even on a repeated same-end request; a disappearing head cannot be hidden by
previously cached data. Frozen overlap prices cannot overwrite the canonical prefix. A real
candle newly appearing in an overlap that was previously omitted and not frozen is accepted.

Once frozen, history is immutable **within that cache generation**. A provider correction to
it is ignored. Restart, eviction or service/credential replacement rebuilds from the same
warmup boundary and re-observes settled upstream history. This preserves the seed boundary,
not byte-identical candle content across later upstream revisions: paper/live have never
bound an immutable execution dataset fingerprint. No historical fills, strategy snapshots,
fees, deployment identities or persisted evaluation cursors are rewritten.

### Bounded fetching and sparse scans

One load can issue at most eight top-level `get_range` calls, including confirmation calls.
Each block advances at most 349 intervals; its one overlap keeps the requested range at most
350 candles and below the adapter's 129,600 hard bound. With Coinbase's inclusive pagination
this is at most one **candle** page per range call, not a bound on product/preview/metadata
HTTP calls or on all windows in a worker cycle. Multiple clocks/products/deployments have
separate loads and budgets. This cache is not an account-wide provider pacer.

A sparse settled block is observed twice. Missing candles between real evidence become
flat, zero-volume no-trade bars at the prior real close (ADR 0095), never interpolated prices.
A missing leading candle has no price and is not fabricated. No missing newest or unsettled
bar is filled. Confirmed empty block scans advance a separate scan cursor even when no
candles can yet be appended. Later real evidence permits filling confirmed interior gaps.
A pending block awaiting confirmation is retained across bounded loads, so even a one-call
test budget progresses instead of repeatedly fetching the same first empty/sparse block.
Provider failures propagate and preserve already confirmed prefix/scan progress.

Requests and returned candles are capped to both the requested as-of end and the actual
closed UTC observation boundary. An earlier as-of request discards a pending block whose
later evidence would exceed that cap; cached history is truncated before serving it.

### Warming is not missing data

Until a scan reaches the requested history, the loader raises the shared, precisely typed
`market_data.window_state.WindowCacheWarmingError`, with product, clock, fixed start, scanned
end, requested end and request count. It does **not** return a partial prefix or an empty
missing-data verdict. Once the scan finishes, genuinely empty/short coverage is returned as
such and ordinary fail-closed data gates still apply.

Lifecycle consumers retry without advancing the decision cursor, changing lifecycle state,
or automatically resuming an operator pause/breaker. Candle-independent order reconciliation
and protective supervision must continue; a warming cache is not permission to drop risk
reduction. The lifecycle implementation owns those catches and no-candle supervision. A
best-effort reference load remains an entry-only gate: unfinished reference history yields
no values for that reference, so reference-dependent signals cannot match while protective
exits remain independent. Neither cache nor loader has mutation/order authority.

### Shared and reference clocks

Load shared HTF/extra clocks at the union of their derived required warmups. Reuse supplied
HTF candles only if they include the fixed extra-clock required start, are contiguous, and
end at the expected closed/as-of bar. Otherwise fetch that clock's required window. Do not
loosen snapshot validation. Reference windows derive their fixed start through the same
`closed_bar_required_coverage` helper as research; missing edges still fail closed.

### Demo identity

Demo bars now derive their synthetic price from their absolute UTC minute, with exact Decimal
arithmetic and positive prices throughout the supported datetime domain. They are invariant
under page start, range size, overlap, preview/range access and restart, and remain explicitly
**demo**, never claimed as venue prices. Previously stored demo datasets/fills are not rewritten.

## Costs and limits

- This is ephemeral prefix caching, **not durable O(1) indicator state**. Returned windows,
  indicator arrays and evaluator CPU still grow with lifetime. No constant-time evaluation
  or absolute memory bound is claimed.
- The LRU default is two million retained candles across windows, including pending evidence.
  Eviction drops whole windows, never their seed prefixes. One active window exceeding the
  ceiling is deliberately retained to avoid endless rebuilding; it can exceed that ceiling.
  Candle/Decimal object bytes and peak evaluator memory are not measured by that count.
- Cold 90-day `1m` rebuilding needs about 373 range/candle-page calls (372 prefix plus one
  head) before warmup and sparse confirmations, spread across many bounded loads. Repeated
  eviction, restarts and credential replacement repay that cost. A working set beyond the
  LRU ceiling can thrash/repeatedly rebuild; timely warming under arbitrary fleet/memory
  load is not guaranteed. A healthy incremental load ordinarily needs a small settled
  extension plus one publication-tail call.
- Missing coverage remains fail closed; this adds no ingestion jobs, listing-floor inference,
  staleness exemption, invented head bar, risk-policy change or automatic lifecycle action.
- No new HTTP/CLI request or response shape, global release contract, database migration, or
  execution strategy schema is required by this cache slice. Shared release landing docs and
  lifecycle warming visibility are integrated by the release lead.

## Alternatives

- **Slide a bounded window:** rejected; moves recursive indicator seeds and changes execution.
- **Remove the adapter hard bound:** rejected; unbounded range requests retain repeated lifetime
  I/O and can strand supervision behind hundreds of HTTP pages.
- **Freeze the latest closed bar immediately:** rejected; loses publication-time revisions and
  lets stale cached values hide missing head data.
- **Return empty on a local request budget:** rejected; confuses rebuild progress with missing
  exchange data and can create a permanent unexpected pause after a healthy restart.
- **Persist incremental indicator state now:** deferred as a separate design requiring versioned
  Decimal/state semantics, crash-consistent candle identities and per-indicator migration.
  Exact anchored prefix caching fixes the range lifetime limit without that broader rewrite.

## Verification

Hermetic tests cover more than 129,600 intervals, bounded multi-load cold rebuild, restart and
LRU miss, exact EMA equality to an uncached full prefix, tail corrections/disappearance,
settlement, sparse leading/interior/empty scans and pending confirmation, provider failures,
concurrency, revisions, as-of/lookahead caps, shared-clock reuse/fallback, reference clocks,
lagged crossover values and signal exits. No production connections or operations are used.
Exact commands, results, graph limitations and cross-lane interfaces are recorded in
the slice completion note (removed in the 2026-10 docs cleanup; see git history).
