# 0016: Longer complete 5m datasets without interpolation

- Status: Accepted
- Date: 2026-09-11
- Supersedes: [0014](0014-watchlist-and-5m-research.md) consequence that 4,032 intervals are enough for 5m research coverage

## Context

ADR 0014 added 5m research datasets and a watchlist lookback, then capped one complete ingest at
4,032 intervals so a Coinbase-sized request would fit. That is 14 days of 5m bars. Watch, config,
and the watchlist CHECK already allow `lookback_hours` up to 2,160 (90 days), but
`bounded_lookback_start` and Coinbase `get_historical_range` still clip 5m history to 14 days.

Fourteen days cannot distinguish a 5m edge from noise or support walk-forward. Incremental ingest
only extends **forward** from the first complete backfill, so the historical depth is frozen at that
clipped window. `ingest_once` also requires the **entire** requested range to be complete before
publishing anything; a 90-day atomic fetch fails closed on any hole.

Coinbase already pages at 350 candles with inclusive page ends (ADR 0015). The 4,032 figure is a
product bound, not a paging limit. Interpolation remains forbidden. Paper and live stay 1h until
later sequenced work; this ADR does not arm 5m execution.

`bounded_lookback_start` is a high-blast-radius helper: `inspect_gaps` and `_plan_range` /
`ingest_once` share it.

## Decision

- Size the interval cap so a 2,160-hour 5m lookback is representable (25,920 five-minute bars).
  Coinbase **page** size stays 350. One-hour lookbacks remain `min(requested, 2,160 hours)` via the
  existing watch/config maximum; they must not jump to 25,920 hours.
- Do not raise `lookback_hours` past 2,160 in this change. Ninety days is the first “months” of 5m
  research coverage.
- Initial backfill walks complete UTC-day chunks oldest-first inside the lookback. Each complete day
  is published through the existing `DatasetStore.write` / `extend` path. Incomplete days are not
  interpolated; `inspect-gaps` classifies them.
- Latest-verified coverage is the newest contiguous complete island. Older complete islands stay
  fingerprint-addressable. Incremental forward ingest stays one-bar overlap.
- Completeness is enforced **per chunk**, not on the whole lookback before any publish.

## Consequences

- Agents can bind fingerprint-addressed 5m ranges long enough for walk-forward research when the
  exchange actually has those bars.
- First backfill of a 90-day 5m target issues many Coinbase pages and takes longer; it stays on the
  market-data worker.
- A hole in the middle of lookback splits islands; paper freshness uses the newest complete suffix.
- 5m paper/live, maker-aware backtest, and paper PnL remain separate sequenced decisions. 15m / 30m
  / 1d intervals are still not implemented.

## Alternatives considered

- Keep 4,032 and rely on months of incremental forward ingest: rejected because historical depth
  never grows backward of the first clip.
- Raise the cap but still require one atomic complete 90-day range: rejected because any hole fails
  the entire backfill and publishes nothing.
- Raise `lookback_hours` to six or twelve months now: deferred; needs a watchlist CHECK migration
  and is not required to unblock walk-forward on 90 days.
- Interpolate missing 5m bars: rejected; completeness stays binary and classified.
