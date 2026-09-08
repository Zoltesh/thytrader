# Architecture Overview

## System shape

ThyTrader is a modular monolith deployed as multiple supervised processes. Domain packages share one
repository and release lifecycle, while API and worker processes provide fault and scaling boundaries.

The diagram describes the **target system shape**, not a claim that every responsibility is already
implemented. Today, the browser and HTTP API provide portfolio, market-data, strategy authoring,
backtests, and paper/live deployments of a published 1h strategy. The portfolio worker takes
snapshots; the market-data worker maintains verified 1h datasets; the execution worker evaluates
closed 1h candles and submits maker orders through a paper broker or Coinbase Advanced Trade REST v3.

```text
SvelteKit web UI
      |
      | REST + ThyTrader WebSocket
      v
FastAPI API process ---------------- PostgreSQL
      |                                  |
Portfolio worker ------------------------+
Market-data worker ----------------------+
Execution worker ------------------------+
      |
      +---- Coinbase market-data REST
      +---- Coinbase Advanced Trade REST v3 orders (live only)
      +---- immutable Parquet datasets <----> Polars / DuckDB
```

## Initial components

### Web application

- SvelteKit, Svelte 5, and strict TypeScript.
- Desktop-first responsive interface.
- TradingView Lightweight Charts is the preferred initial real-time charting library.
- The browser never receives exchange secrets.
- Typed clients should be generated from FastAPI's OpenAPI contract where practical.

### API process

FastAPI owns the supported application interface. Its implemented surface is portfolio, portfolio-history,
market-data, worker-state, health, immutable backtest-result retrieval, and the bounded research mutation
contracts below:

- `POST /api/v1/strategies` creates a durable reference draft with a server-owned identity;
- `GET /api/v1/strategies?status=draft` recovers saved editable drafts and `PUT
  /api/v1/strategies/{strategy_id}/versions/{version}` persists a validated replacement;
- `POST /api/v1/strategies/{strategy_id}/publish` validates and publishes that immutable strategy;
- `GET /api/v1/strategies?status=published` lists active immutable versions, while `POST
  /api/v1/strategies/{strategy_fingerprint}/archive` appends an archive marker that hides a version
  from active selection without changing its canonical publication evidence;
- `POST /api/v1/backtests` binds a verified dataset, publishes/reuses the exact research run, and invokes
  the deterministic backtest engine;
- `POST /api/v1/deployments` starts a paper or live runtime for one published fingerprint; pause, resume,
  and stop are explicit subsequent calls.
- `GET /api/v1/operator/*` is the versioned read-only agent/operator diagnostics contract; the matching
  CLI is `thytrader-operator`. Research mutations for agents use `thytrader-research` with `--confirm`.

Drafts are mutable PostgreSQL records guarded
by an opaque monotonically increasing revision, so a stale browser cannot overwrite a newer save. A
successful publication saves the current draft, writes immutable evidence, and consumes the draft in
one PostgreSQL transaction; published canonical definitions remain immutable. Archives are separate
immutable markers rather than a mutation of the content-addressed publication row.

Paper and live share one execution worker and the same published strategy semantics. Live mode is the
arming action and requires Coinbase credentials; demo mode can paper-trade only. Coinbase order JSON
from Advanced Trade REST v3 is the live ledger. Remaining extras stay deferred: extra timeframes,
trailing stops, native brackets/OCO, user-order WebSockets, and a risk-policy registry.

The following remaining target responsibilities must be exposed as supported, tested contracts before
they are described as available:

- UI WebSocket events for runtime ticks.

HTTP route handlers must remain thin. Exchange logic, risk evaluation, strategy evaluation, and persistence belong to domain/application services.

### Worker process

The target continuously running strategy/execution worker owns:

- Coinbase market and user WebSocket sessions;
- strategy scheduling and evaluation;
- risk-policy evaluation;
- order intent, submission, monitoring, and reconciliation;
- synthetic trailing-stop state;
- recovery after restart;
- market-data ingestion and validation;
- health and audit events.

Core automation is not implemented with cron. Containers or a service manager supervise long-lived processes.

The current `thytrader-worker` is a portfolio snapshot worker, not a strategy scheduler. The current
market-data worker is independently supervised and owns historical market-data ingestion/publication
plus the public Coinbase ticker-feed lifecycle and its durable feed-health evidence. Paper and live
execution run in `thytrader-execution-worker`, which polls closed 1h candles over REST and talks to a
paper broker or the Coinbase REST v3 adapter. Pause continues synthetic stop/time-exit handling and
fill matching but blocks new entries; stop cancels resting orders. The worker replays contiguous
missed closed hours after downtime and pauses when the latest bar is missing or gapped. User-order
WebSockets and trailing-stop workers remain deferred.

Market-data ingestion is already split into its own supervised process so its filesystem publication,
provider failures, and retry loop cannot overlap the portfolio-history worker. This is an operational
boundary within the modular monolith, not a microservice or trading-authority boundary.

### Storage

- **PostgreSQL:** configurations, strategy versions, runtime state, orders, fills, positions, risk state, jobs, and audit records.
- **Parquet:** immutable or append-oriented historical market datasets, partitioned by provider/product/timeframe/date as appropriate.
- **Polars:** primary dataframe/query engine in Python.
- **DuckDB:** ad hoc analytical SQL over Parquet and derived datasets.

Operational correctness must not depend on DuckDB or a dataframe remaining resident in memory.

## Domain boundaries

Expected durable boundaries include:

- `exchanges`: provider-neutral account, market-data, and broker interfaces;
- `market_data`: normalized products, candles, trades, ingestion, and quality checks;
- `strategies`: schemas, indicators, conditions, signals, and versioning;
- `backtesting`: clocks, events, fills, metrics, and reproducibility;
- `execution`: order intents, lifecycle, idempotency, and reconciliation;
- `risk`: composable pre-trade and runtime policies;
- `portfolio`: balances, positions, valuation, and exposure;
- `observability`: health, metrics, structured logs, and audit events.

Dependencies should point toward stable domain abstractions. Coinbase-specific response objects must not leak throughout the system.

## Portability and deployment

### Development

- Python dependencies and commands through `uv`.
- SvelteKit through a pinned Node package manager and lockfile.
- Native processes for fast iteration.

### Supported installation

Docker Compose should provide:

- web, API, portfolio worker, market-data worker, execution worker, and PostgreSQL services;
- health checks and restart policies;
- migrations before service readiness;
- persistent volumes for PostgreSQL, Parquet, and required application state;
- an `.env.example` containing names and placeholders only;
- a setup command that validates configuration before startup.

Default services bind to loopback. Remote access is an explicit deployment profile, not an accidental side effect.

## Evolution to Rust

Rust is a future implementation option for measured hot paths such as feed handling, event processing, order-book simulation, or execution components. Extraction should occur only after profiling shows a material benefit. Stable message/domain contracts make that evolution possible; speculative microservices do not.

Internet-connected Coinbase trading should not be marketed as true HFT merely because a component is written in Rust. Exchange and network latency, data quality, execution design, and risk controls dominate.
