# Product Vision and Scope

## Vision

ThyTrader is an open-source, local-first **Coinbase-first research and trading platform** that a
user or an authorized agent controls. Other exchanges come later, after the Coinbase Advanced Trade
**spot** path is trustworthy.

It brings portfolio visibility, on-demand trading, declarative strategy design, historical and
cross-market research, backtesting, risk management, and automated paper/live execution into one
repository.

The **agent surface is the primary product** ([ADR 0030](../decisions/0030-agent-e2e-primary-surface.md)).
A modern professional UI still matters and must remain capable, but an agent that can drive
ThyTrader end to end is the completeness bar: learn patterns, keep trade journals, analyze
sentiment, research markets, build and backtest strategies, deploy paper and live, monitor
positions, and notify the user.

The first install targets a single user running ThyTrader on a workstation or private VM.
Multi-user tenancy, hosted SaaS, and additional exchanges stay deferred until the core Coinbase
path is trustworthy.

## Product end state

Accepted destination ([ADR 0031](../decisions/0031-coinbase-first-platform-end-state.md)). Not all
of this is shipped; the [roadmap](../roadmap.md) sequences slices. Do not treat the current narrow
paper/live clocks or indicator catalog as the ceiling.

- **Coinbase-first.** Coinbase Advanced Trade REST v3 and WebSockets, spot only, until this path is
  trustworthy. Other venues later.
- **Portfolio:** view balances, positions, and related history.
- **On-demand trades:** place discretionary trades with stop loss, take profit, and other execution
  params — not only strategy-driven orders. Discretionary actions and strategy signals both create
  an **order intent**; they never bypass risk checks to call Coinbase.
- **Strategy design:** declarative, immutable, versioned strategies using many technical indicators
  on exchange-offered timeframes, including **1m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 1d**, and whatever else
  Coinbase lists.
- **Research:** historical and cross-market analysis on complete ingested data (no interpolated
  candles).
- **Deploy single-asset** strategies to paper or live.
- **Deploy multi-asset** strategies to paper or live.
- **Automation:** once deployed, the runtime runs the strategy without babysitting. Pause, resume,
  and stop remain explicit operator/agent actions.
- **Agent E2E:** an agent can do 100% of the above through confirmation-gated, auditable tools
  (`--confirm`; live also `--i-understand-live`).

A user or an agent can research markets, design strategies, backtest them, paper-trade them, and
run live Coinbase spot — with the **same published strategy semantics** in every mode.

## Product principles

1. **Safety before fee optimization.** Prefer maker execution where appropriate, but never let fee savings override emergency risk reduction.
2. **One strategy, multiple runtimes.** Backtest, paper, and live execution consume the same versioned strategy definition and risk policies.
3. **Explicit assumptions.** Backtests disclose fill, latency, fee, spread, and slippage models rather than implying unrealistic precision.
4. **Local-first security.** Secrets remain server-side, network exposure is opt-in, and safe defaults work immediately.
5. **Modular without premature distribution.** Domain boundaries must be clear enough to extract services or Rust components later, while the initial system remains operable as a modular monolith.
6. **Portable operations.** A supported installation should work on another user's machine or private VM without hand-built infrastructure.
7. **Observable and auditable.** Users and authorized agents should be able to understand system health, strategy decisions, orders, fills, and performance without reading sensitive raw storage.
8. **Agent-operable first.** A capability is incomplete until it has a supported, versioned, confirmation-gated agent contract. A polished UI is not a substitute ([ADR 0030](../decisions/0030-agent-e2e-primary-surface.md)).

## Operating models

ThyTrader supports three operating models. All remain first-class **supported** ways to use the
product; none is deprecated, and nothing requires an agent. Agent-driven E2E is the **primary
design target** ([ADR 0030](../decisions/0030-agent-e2e-primary-surface.md)):

1. **100% human-driven.** A person performs every observation and mutation through the browser and CLIs, with the same confirmation gates any actor faces.
2. **100% agent-driven.** An agent performs diagnosis, data ingest, research, journals, notifications, and paper/live control end-to-end through the shipped skills, acting only within explicitly granted, confirmation-gated authority (`--confirm`; live additionally `--i-understand-live`).
3. **Collaborative human + agent.** A human and an agent share the loop—for example, the agent diagnoses and drafts while the human publishes and arms.

Safety comes from confirmation gating, scoped authority, immutable evidence, auditability, and risk
controls—not from excluding agents, and agents are never required.

## Initial user

A technically comfortable individual who wants to:

- connect a Coinbase account using their own API credentials;
- view balances, exposures, and portfolio performance;
- place on-demand trades with SL/TP and other execution params when they choose not to wait for a strategy;
- collect and inspect market history across Coinbase-listed timeframes;
- define strategies through agent skills and a clean UI, with many indicators;
- backtest strategies with credible assumptions and compare across markets;
- deploy a published strategy to paper or live for one instrument or several;
- arm continuous live execution and leave the worker to run it;
- manage SL/TP and trailing exits;
- inspect health, risk state, and execution history;
- authorize an agent to do the same loop, including monitoring and notifying them.

## Shipped slice (honest present)

This is what the running product actually does today. It is **not** the end state.

### Exchange and products

- Coinbase Advanced Trade REST v3 and WebSocket APIs.
- Spot products only.
- Maker-first entries and ordinary take-profit exits.
- Marketable/taker emergency exits when required for capital protection.

### Market data

Shipped complete-only datasets: **1m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 1d**. Strategy, paper, live,
discretionary, and research HTF clocks are that same venue set
([ADR 0040](../decisions/0040-venue-strategy-paper-live-htf-clocks.md)). Paper and live evaluate
`htf_filter` on last-completed complete-only HTF bars
([ADR 0041](../decisions/0041-paper-live-htf-filter-evaluation.md)). Sub-hour live pauses unless the
authenticated user-order feed is connected.

Coinbase candle requests are bounded (currently 350 buckets per request). Ingestion must paginate,
deduplicate, validate, and detect gaps. Missing candles are never interpolated. The data-provider
boundary must permit other historical sources without coupling them to Coinbase execution.

### Strategy authoring

- Structured rule builder with nested AND/OR groups.
- Reusable, parameterized templates.
- A canonical, immutable, versioned strategy schema.
- Future visual node-canvas and custom Python strategy adapters over the same domain interfaces.

The first reference strategy is an EMA trend strategy with optional RSI and volume filters, ATR-based risk, configurable reward/risk take-profit, trailing-stop support, volatility-aware sizing, and maker-entry policies.

### Runtime modes

- Portfolio/read-only mode.
- Backtesting.
- Paper execution through an internal simulated broker.
- Explicitly armed live execution.

Coinbase's static sandbox is suitable for API contract tests, not realistic paper trading; ThyTrader therefore owns its simulation semantics.

On-demand (discretionary) trades with SL/TP are **shipped** as long-only books through the
order-intent → risk → broker path ([ADR 0039](../decisions/0039-on-demand-discretionary-trades.md)).
Strategy deploy of a published fingerprint remains the automated runtime.

### Delivery order for the first usable automation path

The product first made one narrow research loop user-controllable: configure the implemented
reference strategy through the browser, publish an immutable version, backtest it against a verified
dataset, and inspect the evidence. Supported agent observation uses `thytrader-operator`; bounded,
confirmation-gated research automation uses `thytrader-research --confirm`. Paper deployment is the
first automated runtime, using the shared published strategy semantics and independent risk gate.
Guarded live execution remains after paper restart, stale-data, duplicate-event, and reconciliation
acceptance tests pass.

That slice is **shipped**. Later work follows the [roadmap](../roadmap.md). Destination remaining
items include extra exchanges, shorting, attached entry brackets, and
multi-instrument strategy documents. Venue strategy/paper/live/HTF clocks are shipped
([ADR 0040](../decisions/0040-venue-strategy-paper-live-htf-clocks.md)). Per-indicator timeframes
are shipped ([ADR 0042](../decisions/0042-per-indicator-timeframes.md)). Phase 10's risk-policy
registry and concurrent single-instrument paper/live are shipped. Phase 11's walk-forward / OOS /
cross-market studies are shipped. Parameter sweeps, walk-forward optimization, and stitched OOS
equity are shipped ([ADR 0044](../decisions/0044-parameter-sweeps-wfo-stitched-equity.md)).
Phase 12's agent playbook and default-off YOLO opt-in are shipped.
YOLO skip-confirm for live start/pause/resume/stop is shipped
([ADR 0043](../decisions/0043-yolo-live-skip-confirm.md)); `--i-understand-live` remains.
Phase 13's 5m live, ATR trailing, user-order WebSockets, and native OCO brackets are shipped.

## Planned direction: agents as crypto-trading experts (hooks shipped)

A major product goal is for agents to act as **crypto-trading experts that improve from durable
evidence** spanning market-data research, reproducible backtests, paper trades, and live trades.
Phase 14 shipped origin-attributed **hooks** ([ADR 0037](../decisions/0037-phase-14-experiential-memory.md));
there is still no model training:

- Durable journals, sentiment snapshots, and pattern observations with required `origin` (`human` or
  `agent`) so later learning can separate authors.
- Read-only monitor of deployments, recent journals, and notification delivery.
- Config-gated user notification (`none` default, `log`, or `webhook`).
- Improvement stays subordinate to existing invariants: confirmation gates, scoped authority,
  immutable evidence, auditability, and risk controls. It is never a substitute for audit trails.
  YOLO never covers memory mutations.

See [roadmap Phase 14](../roadmap.md#phase-14-experiential-memory--hindsight--shipped).

## Explicitly deferred

- Hosted multi-tenant SaaS.
- Derivatives and leverage.
- **Additional exchanges** until the Coinbase spot path is trustworthy.
- True high-frequency trading claims.
- Full order-book queue simulation in the first backtester.
- Mobile-first UX.
- A visual strategy node canvas in V1.
- Unrestricted agent control over live trading.
- Weakening live-trading safety to make agent or UI convenience easier.
- Treating a polished UI as a substitute for agent-operable APIs and skills.

## Success criteria

### First complete vertical slice (met)

A user can install ThyTrader, configure an operator-selected Coinbase key, inspect portfolio data, load validated market history, configure the reference strategy, run a reproducible backtest, paper-run the same definition, explicitly arm it with conservative limits, execute/reconcile orders, survive a process restart, and review every relevant decision in the audit trail.

### Destination (not met)

A user **or** an authorized agent can research across Coinbase timeframes and products, author
single- and multi-asset strategies with a wide indicator catalog, backtest them, place on-demand
trades with SL/TP, deploy paper or live, leave automation running, monitor, journal, and get
notified — all through confirmation-gated contracts, without interpolating candles or bypassing
risk.
