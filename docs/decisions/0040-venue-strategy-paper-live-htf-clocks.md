# 0040: Venue clocks for strategy, paper, live, and HTF

- Status: Accepted
- Date: 2026-09-16
- Relates to: [0005](0005-canonical-strategy-schema.md), [0018](0018-5m-paper-not-live.md),
  [0019](0019-ops-contract-identity.md), [0025](0025-multi-timeframe-htf-filter.md),
  [0031](0031-coinbase-first-platform-end-state.md), [0036](0036-phase-13-live-extras.md),
  [0038](0038-complete-only-1m-2h-4h-datasets.md), [0039](0039-on-demand-discretionary-trades.md),
  [0041](0041-paper-live-htf-filter-evaluation.md)

## Context

Complete-only Parquet already exists for every Coinbase Advanced Trade candle granularity ThyTrader
ingests: `1m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, and `1d`
([ADR 0038](0038-complete-only-1m-2h-4h-datasets.md) and Phase 7). Strategy `timeframe`, paper,
live, and research `htf_filter` clocks were still `1h`/`5m` LTF and `{15m, 30m, 1h, 6h, 1d}` HTF.
[ADR 0031](0031-coinbase-first-platform-end-state.md) records those remaining clocks as destination
**after** datasets, each widened by ADR. On-demand books ([ADR 0039](0039-on-demand-discretionary-trades.md))
store the same execution clock.

Widening ingest did not interpolate candles and must not start interpolating now. Paper and live
still reject `htf_filter`. Extra exchanges, shorting, and YOLO-without-confirm for live stay out.

## Decision

Use every ingested complete-only venue granularity as a legal strategy LTF, paper clock, live clock,
discretionary book clock, and research HTF token.

### Decision and execution clocks

`StrategyDefinition.timeframe`, paper, live, and discretionary `timeframe` ∈
`{1m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 1d}`. Backtest, paper, and live consume that published (or
stored discretionary) clock. Missing latest bars still pause. No interpolated candles.

Sub-hour live (`1m`, `5m`, `15m`, `30m`) pauses unless the authenticated user-order feed is
connected, the same microstructure gate [ADR 0036](0036-phase-13-live-extras.md) applied to 5m.
`1h` and coarser live still reconcile through REST; a down user feed is reported but does not by
itself pause those clocks.

### HTF

`htf_filter.timeframe` may be any ingested venue clock that is **strictly coarser** than LTF and an
integer multiple of LTF duration. Examples: `1m` LTF may use `5m`/`15m`/`30m`/`1h`/`2h`/`4h`/`6h`/`1d`;
`1h` LTF may use `2h`/`4h`/`6h`/`1d`; `4h` LTF may use `1d` only (`6h` is not an integer multiple).
`1m` cannot be HTF (nothing in the catalog is finer). Alignment, last-completed HTF bars, and
research fingerprinting stay [ADR 0025](0025-multi-timeframe-htf-filter.md). Paper and live still
reject publications that declare `htf_filter`.

### Persistence and ops contract

Alembic `0026` (after discretionary `0025`) widens `ck_deployments_timeframe` and the discretionary
arm of `ck_deployments_kind_identity`. Watchlist CHECKs already include these TFs from `0024`.
Ops contract becomes `thytrader-ops-contract-v12` with `paper_timeframes` and `live_timeframes`
equal to the ingested venue set and `expected_schema_revision` `0026`.

This extends ADRs 0005, 0025, and 0036's clock sets. It does not supersede 0030, 0031, 0034–0039.

## Consequences

- A published `1m`, `15m`, `30m`, `2h`, `4h`, `6h`, or `1d` strategy can research, paper, and arm
  live the same way `1h`/`5m` already could, against a matching complete-only dataset.
- Discretionary books accept those clocks without changing intent persistence, risk, OCO, or
  reconcile-before-retry.
- Agent CLIs fail closed on a v11 image (`paper_timeframes` / `live_timeframes` mismatch) until
  `make run`.
- Paper/live HTF evaluation was out of this clock slice.
  [ADR 0041](0041-paper-live-htf-filter-evaluation.md) later evaluated `htf_filter` in paper and live.
  Per-indicator timeframes, extra exchanges, shorting, and YOLO-without-confirm for live stay out.

## Alternatives considered

- **Widen only `1m`/`2h`/`4h` and leave `15m`/`30m`/`6h`/`1d` as HTF-only:** rejected; ADR 0031
  requires strategy/paper/live clocks to match ingested venue TFs.
- **Evaluate `htf_filter` in paper/live:** rejected; that is a separate HTF-candle worker slice.
- **Interpolate incomplete days so 1m live can run on holes:** rejected; completeness stays binary.
- **Require the user-order feed for 1h+ live:** rejected; REST reconcile remains sufficient for
  those clocks (ADR 0036).
- **Rewrite discretionary order placement:** rejected; only the stored clock set widens.
