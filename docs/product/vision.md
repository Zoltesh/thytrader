# Product Vision and Scope

## Vision

ThyTrader is an open-source, local-first trading workstation that a user controls. It brings portfolio visibility, strategy design, historical research, backtesting, risk management, and automated execution into one repository and one coherent interface.

The first release targets a single user running ThyTrader on a workstation or private VM. Multi-user tenancy, hosted SaaS concerns, and broad exchange coverage are deliberately deferred until the core trading system is trustworthy.

## Product principles

1. **Safety before fee optimization.** Prefer maker execution where appropriate, but never let fee savings override emergency risk reduction.
2. **One strategy, multiple runtimes.** Backtest, paper, and live execution consume the same versioned strategy definition and risk policies.
3. **Explicit assumptions.** Backtests disclose fill, latency, fee, spread, and slippage models rather than implying unrealistic precision.
4. **Local-first security.** Secrets remain server-side, network exposure is opt-in, and safe defaults work immediately.
5. **Modular without premature distribution.** Domain boundaries must be clear enough to extract services or Rust components later, while the initial system remains operable as a modular monolith.
6. **Portable operations.** A supported installation should work on another user's machine or private VM without hand-built infrastructure.
7. **Observable and auditable.** Users and authorized agents should be able to understand system health, strategy decisions, orders, fills, and performance without reading sensitive raw storage.

## Initial user

A technically comfortable individual who wants to:

- connect a Coinbase account using their own API credentials;
- view balances, exposures, and portfolio performance;
- collect and inspect market history;
- define strategies through a clean UI;
- backtest strategies with credible assumptions;
- optionally paper trade;
- arm strategies for continuous live execution;
- manage SL/TP and trailing exits;
- inspect health, risk state, and execution history.

## V1 scope

### Exchange and products

- Coinbase Advanced Trade REST v3 and WebSocket APIs.
- Spot products only.
- Maker-first entries and ordinary take-profit exits.
- Marketable/taker emergency exits when required for capital protection.

### Market data

Required candle intervals:

- 5 minutes
- 15 minutes
- 30 minutes
- 1 hour
- 6 hours
- 1 day

Coinbase currently exposes these granularities, with a maximum of 350 buckets per candle request. Ingestion must paginate, deduplicate, validate, and detect gaps. The data-provider boundary must permit other historical sources without coupling them to Coinbase execution.

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

### Delivery order for the first usable automation path

The product will first make one narrow research loop user-controllable: configure the implemented
reference strategy through the browser, publish an immutable version, backtest it against a verified
dataset, and inspect the evidence. Supported agent observation follows that loop; bounded,
confirmation-gated agent research automation may follow once the same browser/API contracts are
tested. Paper deployment is the first automated runtime, using the shared published strategy semantics
and independent risk gate. Guarded live execution remains after paper restart, stale-data, duplicate-
event, and reconciliation acceptance tests pass.

## Explicitly deferred

- Hosted multi-tenant SaaS.
- Derivatives and leverage.
- Multiple exchanges in the initial release.
- True high-frequency trading claims.
- Full order-book queue simulation in the first backtester.
- Mobile-first UX.
- A visual strategy node canvas in V1.
- Unrestricted agent control over live trading.

## Success criteria for the first complete vertical slice

A user can install ThyTrader, configure an operator-selected Coinbase key, inspect portfolio data, load validated market history, configure the reference strategy, run a reproducible backtest, paper-run the same definition, explicitly arm it with conservative limits, execute/reconcile orders, survive a process restart, and review every relevant decision in the audit trail.
