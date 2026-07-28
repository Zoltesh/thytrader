# Delivery Roadmap

This roadmap sequences capabilities and safety gates. It is not a promise of dates. Each phase should produce a usable, tested vertical increment rather than a collection of disconnected scaffolds.

## Phase 0: Repository foundation — ✅ Complete

- Architecture and product documentation.
- Repository-specific `AGENTS.md`.
- GitNexus semantic and PDG indexing workflow.
- Python package layout and FastAPI application skeleton.
- SvelteKit/Svelte 5 strict-TypeScript application.
- Formatting, linting, type checking, tests, and CI.
- Docker Compose clone-and-run stack for PostgreSQL, migration, API, worker, and web.
- PostgreSQL migrations and configuration validation.
- `.env.example` with placeholders only.
- Structured logging and secret redaction.

**Exit gate met:** one documented command (`uv run python scripts/setup_local_stack.py`) starts
healthy web, API, worker, and database services, applies migrations before long-running services,
and passes quality checks on a clean checkout.

## Phase 1: Read-only Coinbase and portfolio visibility — In progress

### Completed

- Provider-neutral exchange account contracts (`exchanges/protocols.py`, `exchanges/models.py`).
- Coinbase Advanced Trade adapter using the official SDK (`exchanges/coinbase.py`).
- Coinbase authentication and credential configuration with `SecretStr`-backed values.
- Permission display without rejecting keys that have additional permissions.
- Account balances, portfolio valuation (Decimal-precise), and demo fallback.
- Scheduled snapshot worker: startup observation, configurable interval, demo skip, error retry.
- Persisted portfolio valuation history with append-only snapshots.
- Read-only history chart with range filtering (24H/7D/30D/All), gain/loss, gap handling, and freshness.
- Dashboard with connection status, staleness indicators, and redacted diagnostics.
- API `/health/ready` and `/health/live` endpoints.
- Secret redaction in logs, test fixtures, and API responses.

### Remaining before exit gate

- Coinbase market/user WebSocket lifecycle and heartbeat handling.
- Fee-tier display and transaction-cost visibility.
- Structured audit-event recording (append-oriented events for connection transitions, snapshots, and errors).
- Market-data freshness indicators on the dashboard (separate from portfolio snapshot staleness).

**Exit gate:** a user can connect an operator-selected key and observe an accurate, reconcilable
portfolio — including live market data, fees, and audit trail — without enabling order submission.

## Phase 2: Historical data and strategy definitions

This is the next active milestone. It has two parallel tracks that converge before Phase 3.

### Phase 2A: Market-data pipeline

- Product/instrument catalog (product IDs, trading status, base/quote, price/size increments, minimum sizes).
- Historical OHLCV ingestion for 5m, 15m, 30m, 1h, 6h, and 1d.
- Pagination, deduplication, gap detection, and completeness metadata.
- Partitioned Parquet storage and dataset versioning.
- Scheduled market-data worker (separate from the portfolio snapshot worker).
- Data-quality status on the dashboard.

**Exit gate:** validated, gap-checked historical candles are queryable with dataset fingerprints that
backtests can reference for reproducibility.

### Phase 2B: Canonical strategy schema

- Backend-validated, immutable, versioned declarative strategy schema (see
  [canonical strategy schema](architecture/canonical-strategy-schema.md)).
- Indicator registry (EMA, RSI, ATR, SMA, volume SMA).
- Nested AND/OR condition builder with typed operators and temporal semantics.
- Reference EMA trend strategy template.
- Strategy versioning: draft → published → archived; editing creates a new version.
- Human-readable strategy summaries.

**Exit gate:** the same immutable strategy version can be validated and associated with a
reproducible dataset snapshot.

## Phase 3: Backtesting

- Event-driven simulation kernel with strict no-lookahead enforcement.
- Conservative bar-level broker with fees, spread, slippage, latency, and rejection models.
- Portfolio/risk policy integration.
- SL/TP and trailing-stop state machines.
- Reproducible results: equity/drawdown series, trade ledger, metrics, and disclosed assumptions.
- Out-of-sample and walk-forward workflow.
- Benchmark comparison (buy-and-hold).

**Exit gate:** reference-strategy results are deterministic, disclose assumptions, resist lookahead,
and pass adversarial fill/risk tests.

## Phase 4: Paper execution

- Persistent simulated broker using live market events.
- Same strategy and risk path used by backtests/live trading.
- Continuous worker supervision.
- Restart recovery and reconciliation tests.
- Runtime monitoring, pause/resume, and kill switches.

**Exit gate:** paper strategies survive forced restarts and ambiguous events without duplicated
orders or lost state.

## Phase 5: Guarded live execution

- Explicit live arming and conservative defaults.
- Order preview where useful.
- Idempotent maker entries and ordinary take-profit orders.
- Timeout, repricing, cancellation, and reconciliation policies.
- Coinbase-native stops/brackets where semantics fit.
- Marketable emergency exits.
- Synthetic trailing-stop worker.
- Operator runbook and failure drills.

**Exit gate:** live trading begins only after read-only, paper, restart, reconciliation,
disconnect, stale-data, and kill-switch acceptance tests pass.

## Phase 6: Operator and agent integration

- Stable versioned read-only diagnostics API and CLI.
- Redacted health/configuration/data-quality/performance reports.
- In-repo ThyTrader operator skill.
- Machine-readable schemas and compatibility checks.
- Explicitly separated, confirmation-gated mutation tools if later justified.

**Exit gate:** an external agent can diagnose a running instance using supported interfaces
without database access, secret exposure, or implicit trading authority.
