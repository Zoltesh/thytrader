# Architecture Overview

## System shape

ThyTrader is a modular monolith deployed as multiple supervised processes. Domain packages share one
repository and release lifecycle, while API and worker processes provide fault and scaling boundaries.

The diagram describes the **target system shape**, not a claim that every responsibility is already
implemented. Today, the browser and HTTP API provide read-only portfolio, market-data, and backtest
evidence plus a bounded research-only mutation flow: a browser draft can be validated/published as an
immutable strategy and submitted to the deterministic historical backtest service. The portfolio worker
takes snapshots; and the independently supervised market-data worker maintains verified 1h datasets.
There is no strategy runtime worker, paper broker, order management, risk, reconciliation, or
live-execution package yet.

```text
SvelteKit web UI
      |
      | REST + ThyTrader WebSocket
      v
FastAPI API process ---------------- PostgreSQL
      |                                  |
Portfolio worker ------------------------+
Market-data worker ----------------------+
      |
      +---- Coinbase market-data REST
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

- `POST /api/v1/strategies` creates an ephemeral reference draft with a server-owned identity;
- `POST /api/v1/strategies/{strategy_id}/publish` validates and publishes that immutable strategy;
- `POST /api/v1/backtests` binds a verified dataset, publishes/reuses the exact research run, and invokes
  the deterministic backtest engine.

These contracts have no paper/live execution authority. The strategy draft is intentionally browser-local;
durable draft/archive lifecycle remains future work.

The following are target responsibilities that must be exposed as supported, tested contracts before
they are described as available:

- strategy configuration and versioning;
- backtest submission and result retrieval;
- runtime status and health;
- explicit live-trading arm/disarm operations;
- UI WebSocket events;
- future read-only operator/agent endpoints.

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
market-data worker is independently supervised and only owns market-data ingestion/publication.
Paper and live workers must not be inferred from either process merely existing.

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

- web, API, worker, and PostgreSQL services;
- health checks and restart policies;
- migrations before service readiness;
- persistent volumes for PostgreSQL, Parquet, and required application state;
- an `.env.example` containing names and placeholders only;
- a setup command that validates configuration before startup.

Default services bind to loopback. Remote access is an explicit deployment profile, not an accidental side effect.

## Evolution to Rust

Rust is a future implementation option for measured hot paths such as feed handling, event processing, order-book simulation, or execution components. Extraction should occur only after profiling shows a material benefit. Stable message/domain contracts make that evolution possible; speculative microservices do not.

Internet-connected Coinbase trading should not be marketed as true HFT merely because a component is written in Rust. Exchange and network latency, data quality, execution design, and risk controls dominate.
