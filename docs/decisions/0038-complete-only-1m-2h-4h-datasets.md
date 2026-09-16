# 0038: Complete-only 1m, 2h, and 4h historical datasets

- Status: Accepted
- Date: 2026-09-15
- Relates to: [0014](0014-watchlist-and-5m-research.md), [0016](0016-longer-complete-5m-datasets.md),
  [0019](0019-ops-contract-identity.md), [0020](0020-complete-only-15m-datasets.md),
  [0021](0021-complete-only-30m-datasets.md), [0022](0022-complete-only-6h-datasets.md),
  [0023](0023-complete-only-1d-datasets.md), [0031](0031-coinbase-first-platform-end-state.md)

## Context

Phase 7 shipped complete-only Parquet for 5m, 15m, 30m, 1h, 6h, and 1d. Coinbase Advanced Trade
lists additional candle granularities that were still missing from datasets: `ONE_MINUTE` (`1m`),
`TWO_HOUR` (`2h`), and `FOUR_HOUR` (`4h`). [ADR 0031](0031-coinbase-first-platform-end-state.md)
records those as destination **datasets then clocks**. This ADR is the dataset slice only.

Strategy schema, paper, and live clocks stay `1h` or `5m`. Widening those clocks at the same time as
ingest would silently let research and execution treat `1m`, `2h`, or `4h` as a strategy timeframe.
Research `htf_filter` stays the shipped coarser set (`15m`/`30m`/`1h`/`6h`/`1d`).

A 2,160-hour watch of 1m bars is 129,600 candles. [ADR 0016](0016-longer-complete-5m-datasets.md)
sized `MAX_HISTORICAL_INTERVAL_COUNT` to 25,920 so a 90-day 5m lookback was representable; that cap
would clip a 90-day 1m watch to 18 days. Ingest still publishes complete UTC-day chunks (1,440 1m
bars per day), so the product cap is only the bound for one diagnostic/provider range, not a reason
to interpolate.

## Decision

- Add `CandleInterval.ONE_MINUTE` (`1m`), `TWO_HOURS` (`2h`), and `FOUR_HOURS` (`4h`) for **dataset**
  ingest, publication, verification, gap classification, diagnostics, and latest-dataset catalog.
- One-minute bars have exact one-minute duration and UTC close alignment on each minute. A complete
  UTC day contains exactly 1,440 aligned candles.
- Two-hour bars have exact two-hour duration and UTC close alignment at even hours (`00:00`,
  `02:00`, …, `22:00`). A complete UTC day contains exactly twelve aligned candles.
- Four-hour bars have exact four-hour duration and UTC close alignment at `00:00`, `04:00`, `08:00`,
  `12:00`, `16:00`, and `20:00`. A complete UTC day contains exactly six aligned candles.
- Keep the existing complete-only pipeline: closed UTC bars, no interpolation, worker-only
  publication, fingerprint-addressed Parquet plus manifest, UTC-day chunks, `watch_complete`.
- Size `MAX_HISTORICAL_INTERVAL_COUNT` to 129,600 so a 2,160-hour 1m lookback is representable.
  Coinbase **page** size stays 350. One-hour watches remain `min(requested, 2,160 hours)`.
- Do **not** widen `StrategyDefinition.timeframe`, paper/live ops-contract clocks, research warmup
  inference, or `htf_filter` to `1m`, `2h`, or `4h`. Dataset binding still requires matching product
  and timeframe, so a 1h/5m strategy cannot consume these datasets.
- Watchlist CHECK and ops-contract `expected_schema_revision` move to Alembic `0024` (after Phase 14
  `0023`). Bump `OPS_CONTRACT_ID` to `thytrader-ops-contract-v10` per
  [0019](0019-ops-contract-identity.md). Downgrade restores the 1h/5m/15m/30m/6h/1d constraint;
  existing `1m`/`2h`/`4h` rows must be removed first.

## Consequences

- Operators can watch, ingest, inspect, and catalog 1m, 2h, and 4h coverage the same way as 5m.
- These fingerprints appear in the verified dataset catalog; they are not LTF, paper, live, or HTF
  clocks.
- Strategy/paper/live clocks for these granularities remain a later ADR, the same way 0020–0023
  added datasets without silently making those TFs legal execution clocks.

## Alternatives considered

- Wait until strategy `timeframe` includes `1m`/`2h`/`4h`: rejected; ingest is independently useful
  and is the documented datasets-then-clocks first half.
- Add these TFs to strategy, paper, live, or `htf_filter` together: rejected; execution and HTF
  clocks are explicit follow-ups, not a side effect of datasets.
- Omit `4h` because ADR 0031 named only `1m` and `2h`: rejected; Coinbase Advanced Trade lists
  `FOUR_HOUR`, and 0031 includes any additional listed interval.
- Interpolate incomplete days: rejected; completeness stays binary and classified.
- Keep the 25,920-bar cap and clip 1m watches to 18 days: rejected; 90-day lookback is the existing
  watch maximum for every other dataset timeframe.
