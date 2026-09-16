# 0041: Paper and live HTF-filter evaluation

- Status: Accepted
- Date: 2026-09-16
- Relates to: [0005](0005-canonical-strategy-schema.md), [0019](0019-ops-contract-identity.md),
  [0025](0025-multi-timeframe-htf-filter.md), [0030](0030-agent-e2e-primary-surface.md),
  [0031](0031-coinbase-first-platform-end-state.md),
  [0040](0040-venue-strategy-paper-live-htf-clocks.md)

## Context

Research already evaluates optional `htf_filter` as a closed-bar AND with LTF entry
([ADR 0025](0025-multi-timeframe-htf-filter.md)). Alignment uses the last HTF bar whose exclusive
close is `≤` the LTF close; in-progress HTF bars are never used; missing HTF coverage fails closed.
[ADR 0040](0040-venue-strategy-paper-live-htf-clocks.md) widened LTF and HTF tokens to every ingested
venue clock and left paper/live rejecting those publications.

Silently ignoring `htf_filter` in paper/live would trade without the published filter. Inventing HTF
candles would interpolate. Per-indicator timeframes, extra exchanges, shorting, and
YOLO-without-confirm for live stay out.

## Decision

Paper and live evaluate published `htf_filter` with the same semantics as research, on complete-only
HTF candles.

### Alignment

At LTF close `T`, HTF indicator values come from the last HTF bar whose exclusive close is `≤ T`.
A same-close HTF bar is eligible. An in-progress HTF bar is never used. Combined entry is the
tri-state AND of HTF `when` and LTF `entry.when`. HTF crossovers compare the mapped previous LTF
bar's HTF values with the current mapped HTF values, so they fire when the HTF bar rolls.

HTF timeframe remains a strictly coarser integer multiple of LTF (ADR 0025 / 0040). `1m` LTF may
use `5m`…`1d`; `4h`/`6h` stays illegal.

### Runtime

The execution worker loads complete-only last-completed HTF bars from the same market-data path as
LTF. Gaps, duplicates, or a missing latest completed HTF bar pause the deployment. They are never
interpolated. Paper and live do not bind a frozen HTF dataset fingerprint; research still does.

Deploying a published HTF-filter strategy is allowed on every ingested venue LTF clock. Live still
requires credentials. Sub-hour live still pauses unless the user-order feed is connected.

### Ops contract

No Alembic revision. Ops contract becomes `thytrader-ops-contract-v13` with
`htf_filter_runtimes` `research`, `paper`, and `live`, and `expected_schema_revision` remaining
`0026`. A v12 image fails closed until `make run`.

This extends ADR 0025 and the paper/live rejection clause of ADR 0040. It does not supersede 0030,
0031, 0034–0039, or 0040's clock set.

## Consequences

- A published HTF-filter strategy can paper and arm live with the same last-completed HTF semantics
  as research V1/V2/V3.
- Stale Compose images that still 409 HTF deployments fail the ops-contract preflight.
- Per-indicator timeframes, extra exchanges, shorting, and YOLO-without-confirm for live stay out.

## Alternatives considered

- **Keep rejecting paper/live HTF:** rejected; the same published document must mean the same thing
  in every runtime (ADR 0005 / 0031).
- **Ignore `htf_filter` in paper/live:** rejected by ADR 0025; that would trade without the filter.
- **Bind a frozen HTF dataset fingerprint at deploy:** rejected; paper/live already consume live
  complete-only closed bars for LTF, and HTF must follow that path without interpolating holes.
- **Per-indicator timeframes:** rejected; still out of ADR 0025 / Phase 9.
