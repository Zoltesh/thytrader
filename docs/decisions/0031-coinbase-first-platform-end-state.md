# 0031: Coinbase-first research and trading platform end-state

- Status: Accepted
- Date: 2026-09-15
- Relates to: [0004](0004-safe-execution-and-access.md), [0005](0005-canonical-strategy-schema.md),
  [0018](0018-5m-paper-not-live.md), [0020](0020-complete-only-15m-datasets.md),
  [0021](0021-complete-only-30m-datasets.md), [0022](0022-complete-only-6h-datasets.md),
  [0023](0023-complete-only-1d-datasets.md), [0025](0025-multi-timeframe-htf-filter.md),
  [0030](0030-agent-e2e-primary-surface.md),
  [0040](0040-venue-strategy-paper-live-htf-clocks.md)

## Context

Shipped ThyTrader is a trustworthy local-first Coinbase spot workstation with portfolio visibility,
complete-only datasets, a declarative strategy schema, deterministic backtests, paper on 1h|5m, live
on 1h, and confirmation-gated agent skills. Product docs described a "trading workstation" and a V1
candle list of 5m, 15m, 30m, 1h, 6h, and 1d. Strategy, paper, and live clocks remain `1h` or `5m`
(live `1h` only) per ADRs 0018 and 0020–0023.

The destination product is broader and must be explicit so later agents do not treat today's narrow
slice as the ceiling:

- Coinbase-first research **and** trading; other exchanges later.
- On-demand (discretionary) trades with stop loss, take profit, and other execution params — not
  only strategy-driven orders.
- Strategy design on **every Coinbase-listed candle granularity**, including 1m, 5m, 15m, 30m, 1h,
  2h, 6h, 1d, and whatever else the venue adds.
- Historical and **cross-market** research on complete ingested data (no interpolated candles).
- **Single-asset and multi-asset** deploy to paper or live.
- Full automation after deploy (the worker runs the strategy without babysitting).
- Agent-driven E2E as the primary surface (ADR 0030).

None of that silently widens today's schema, dataset, paper, or live clocks. This ADR records the
destination; the roadmap sequences the slices.

## Decision

ThyTrader's accepted product destination is a **Coinbase-first research and trading platform**.
Until the Coinbase Advanced Trade **spot** path is trustworthy, do not add another exchange.

End-state capabilities (accepted, **not shipped by this ADR**):

1. **Portfolio** — balances, positions, and related history (read-only portfolio already exists).
2. **On-demand trades** — discretionary orders with stop loss, take profit, and other execution
   params. A discretionary action creates an **order intent** and still passes risk checks; it does
   not call Coinbase directly. Implementation needs a later slice and ADR for venue brackets vs
   synthetic exits, idempotency, and audit origin (human vs agent).
3. **Strategy design** — the same immutable declarative schema (ADR 0005) with a growing fail-closed
   indicator catalog, on exchange-offered timeframes. Named destination granularities: `1m`, `5m`,
   `15m`, `30m`, `1h`, `2h`, `6h`, `1d`, plus any additional Coinbase-listed candle interval.
4. **Research** — historical and cross-market analysis on complete-only ingested data. No candle
   interpolation. Walk-forward / OOS remains roadmap Phase 11.
5. **Deploy single-asset** strategies to paper or live (narrow path already exists).
6. **Deploy multi-asset** strategies to paper or live (roadmap Phase 10: portfolio / risk-policy
   registry beyond one instrument and `max_concurrent_positions = 1`).
7. **Automation** — after a confirmed paper or live deploy, `thytrader-execution-worker` evaluates
   closed bars and manages intents without an operator babysitting each candle. Pause/resume/stop
   remain explicit.
8. **Agent E2E** — confirmation-gated skills cover the capabilities above (ADR 0030).

**What this ADR does not change:**

- Strategy `timeframe`, paper clocks, and live clocks stay `1h`|`5m` (live `1h`) until a later ADR
  widens them the same way 0020–0023 added datasets without silently making those TFs legal LTF or
  execution clocks. [ADR 0040](0040-venue-strategy-paper-live-htf-clocks.md) is that later ADR.
- Complete-only publication, no interpolation, worker-owned ingest, and fingerprint-addressed
  Parquet remain mandatory for every new granularity (including future `1m` and `2h` datasets).
- Backtest, paper, and live still consume the same published strategy semantics.
- Loopback-only default, maker-first entries, risk-first exits, and live `--i-understand-live`
  remain.

## Consequences

- Product vision, roadmap, and architecture overviews must distinguish **destination** from
  **shipped contract**. Agents must not treat `1m`/`2h`, on-demand orders, or multi-asset deploy as
  implemented.
- `1m` and `2h` (and any newly listed Coinbase granularity) are destination dataset then
  strategy/paper/live-clock work. They are **not** inserted ahead of the current Phase 9 remaining →
  14 Builder sequence.
- Discretionary trading is in-scope for the product, out-of-scope for silent implementation. It
  shares the order-intent and risk boundary with strategy-driven orders.
- Additional exchanges stay explicitly deferred.

## Alternatives considered

- **Leave V1 candle list as 5m–1d forever:** rejected; Coinbase already lists `1m` and `2h`, and
  "whatever the venue offers" is the honest destination.
- **Widen strategy/paper/live clocks in this ADR:** rejected; ADRs 0018 and 0020–0023 exist so
  ingest does not silently become an execution clock.
- **Strategy-only (no discretionary orders):** rejected; portfolio users need on-demand trades with
  SL/TP without authoring a strategy.
- **Multi-exchange in parallel with Coinbase:** rejected; financial correctness on one venue
  outranks coverage.
- **Interpolate missing candles to unlock research sooner:** rejected; completeness stays binary
  and classified.
