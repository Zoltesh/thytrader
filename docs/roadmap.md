# Delivery Roadmap

This roadmap sequences capabilities and safety gates. It is not a promise of dates. Each phase should produce a usable, tested vertical increment rather than a collection of disconnected scaffolds.

## Phase 0: Repository foundation

- Architecture and product documentation.
- Repository-specific `AGENTS.md`.
- GitNexus semantic and PDG indexing workflow.
- Python package layout and FastAPI application skeleton.
- SvelteKit/Svelte 5 strict-TypeScript application.
- Formatting, linting, type checking, tests, and CI.
- Docker Compose development/install baseline.
- PostgreSQL migrations and configuration validation.
- `.env.example` with placeholders only.
- Structured logging and secret redaction.

**Exit gate:** one documented command starts healthy web, API, worker, and database services; quality checks pass on a clean checkout.

## Phase 1: Read-only Coinbase and portfolio visibility

- Provider-neutral exchange interfaces.
- Coinbase Advanced Trade authentication and permission validation.
- Reject or block Transfer-enabled keys.
- Account balances, product metadata, fee tier, and portfolio valuation.
- Market/user WebSocket lifecycle and heartbeat handling.
- Desktop dashboard with connection and freshness indicators.
- Redacted diagnostics and audit events.

**Exit gate:** a user can safely connect a least-privilege key and observe an accurate, reconcilable portfolio without enabling order submission.

## Phase 2: Historical data and strategy definitions

- Candle ingestion for 5m, 15m, 30m, 1h, 6h, and 1d.
- Pagination, deduplication, quality validation, and gap repair.
- Partitioned Parquet storage and dataset metadata.
- Canonical versioned strategy schema.
- Indicator and condition registry.
- Nested rule-builder UI and reference strategy template.

**Exit gate:** the same immutable strategy version can be validated and associated with a reproducible dataset snapshot.

## Phase 3: Backtesting

- Event-driven simulation kernel.
- Conservative bar-level broker.
- Fees, spread, slippage, latency, precision, rejection, cancellation, and partial-fill models.
- Portfolio/risk policy integration.
- SL/TP and trailing-stop state machines.
- Reproducible results, metrics, charts, and trade ledger.
- Out-of-sample and walk-forward workflow.

**Exit gate:** reference-strategy results are deterministic, disclose assumptions, resist lookahead, and pass adversarial fill/risk tests.

## Phase 4: Paper execution

- Persistent simulated broker using live market events.
- Same strategy and risk path used by backtests/live trading.
- Continuous worker supervision.
- Restart recovery and reconciliation tests.
- Runtime monitoring, pause/resume, and kill switches.

**Exit gate:** paper strategies survive forced restarts and ambiguous events without duplicated orders or lost state.

## Phase 5: Guarded live execution

- Explicit live arming and conservative defaults.
- Order preview where useful.
- Idempotent maker entries and ordinary take-profit orders.
- Timeout, repricing, cancellation, and reconciliation policies.
- Coinbase-native stops/brackets where semantics fit.
- Marketable emergency exits.
- Synthetic trailing-stop worker.
- Operator runbook and failure drills.

**Exit gate:** live trading begins only after read-only, paper, restart, reconciliation, disconnect, stale-data, and kill-switch acceptance tests pass.

## Phase 6: Operator and agent integration

- Stable versioned read-only diagnostics API and CLI.
- Redacted health/configuration/data-quality/performance reports.
- In-repo ThyTrader operator skill.
- Machine-readable schemas and compatibility checks.
- Explicitly separated, confirmation-gated mutation tools if later justified.

**Exit gate:** an external agent can diagnose a running instance using supported interfaces without database access, secret exposure, or implicit trading authority.

## Later possibilities

- Additional exchanges through existing adapter contracts.
- Visual node-canvas strategy editor.
- Controlled custom Python strategy plugins.
- Tick and order-book backtesting.
- Rust components for profiled hot paths.
- Mobile-optimized views.
- Hosted/multi-user architecture only after a new security and tenancy design.
