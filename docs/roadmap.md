# Delivery Roadmap

This roadmap sequences capabilities and safety gates. It is not a promise of dates. Each phase should produce a usable, tested vertical increment rather than a collection of disconnected scaffolds.

## Current delivery focus: close the user-controllable loop

The numbered phases describe capability boundaries and safety gates; they are **not** a requirement to
finish every earlier checklist before delivering the next user-visible outcome. The implemented
research foundations are intentionally ahead of strategy authoring, paper execution, and agent
integration. The next work therefore prioritizes one narrow, usable path over further horizontal
expansion:

1. **Create and research in the browser:** author a conservative strategy draft, validate and
   publish an immutable version, select a verified dataset, submit a backtest, and inspect the
   resulting evidence. This must reuse the existing canonical publication and deterministic
   backtest services rather than introduce a second strategy or simulation path.
2. **Observe through supported agent interfaces:** expose versioned, redacted, read-only operator
   reports for health, data quality, published strategy state, and backtest evidence; then ship the
   matching read-only `thytrader-operator` skill.
3. **Permit bounded research automation:** only after the browser/API mutation contracts are
   tested, add separate, explicit-confirmation-gated agent tools for strategy drafts, publication,
   and backtest submission. These tools have no paper or live trading authority.
4. **Automate in paper mode:** build the smallest durable paper-deployment loop for the reference
   1h candle-close strategy: scheduling, idempotent per-candle evaluation, simulated orders/fills,
   position and P&L state, independent pre-trade checks, pause/kill controls, restart recovery, and
   a visible runtime screen.
5. **Only then broaden:** multi-timeframe/products, higher-fidelity broker models, and guarded live
   execution follow once the single-product paper loop has passed its failure-mode tests.

The first paper loop does not require Coinbase WebSockets: the existing verified 1h market-data
maintenance path is sufficient for candle-close evaluation. WebSockets remain required before
lower-latency behavior, user-order monitoring, or live reconciliation can be considered complete.

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

Phase 2 remains active. Its market-data and strategy tracks provide the foundations used by the
implemented research path and the next browser author-to-result increment.

### Phase 2A: Market-data pipeline

#### Completed range-ingestion increment

- Read-only USD spot-product catalog plus selectable 1h data-source diagnostics.
- Coinbase product-constraint and bounded recent-candle adapter through the official SDK.
- Closed-candle validation, gap/missing-interval detection, and freshness facts.
- Bounded 1h historical range ingestion with non-overlapping Coinbase pagination (350 candles/page,
  2,160 candle / 90-day maximum).
- Seven-day range-completeness report endpoint with expected vs received counts and binary coverage.
- Immutable date-partitioned Parquet writer with JSON manifests, completeness facts, and SHA-256
  content fingerprints, wired only to the dedicated scheduled ingestion worker.
- Deterministic demo diagnostics plus a visible dashboard connection/integrity panel.
- Separately supervised market-data worker with its own readiness, restart boundary, persistent Parquet
  volume, complete-only publication, automatic retry, and durable PostgreSQL success/failure state.
- Read-only API and dashboard coverage/freshness/fingerprint/failure diagnostics.
- Continuous cumulative 1h maintenance: durable planning from last verified coverage, one-candle
  overlap, deterministic merge, no-op current cycles, immutable revisions, and fingerprint lookup.
- Capped exponential retry scheduling with jitter and prior-revision preservation across failures.

This proves the read-only provider, validation, continuous 1h maintenance, and fingerprint-addressed
dataset paths. It is deliberately **not** a price chart, market signal, or backtest engine. See the
[market-data pipeline](architecture/market-data.md) for its explicit contract and limits.

#### Remaining

- Extend the same durable contract to additional timeframes (5m, 15m, 30m, 6h, 1d).
- Multi-product scheduling and explicit gap-repair workflows beyond idempotent exact-range retries.
- Versioned machine-readable diagnostics and operator CLI.

**1h exit gate met:** validated, gap-checked historical candles are queryable by immutable dataset
fingerprints that future backtests can reference for reproducibility. Multi-timeframe and
multi-product expansion remain before Phase 2A is considered broadly complete.

The gate has live PostgreSQL evidence: the full migration chain runs on PostgreSQL 18, two
independent database engines prove stale retry-generation claims are rejected atomically, and the
installed worker plus deterministic acceptance drill cover initial publication, no-op and
incremental boundaries, corrupt-manifest reconciliation, provider failure, restart backoff,
readiness, and graceful shutdown.

### Phase 2B: Canonical strategy schema — 🚧 In progress

- ✅ Backend-validated immutable publication for the conservative reference profile (see
  [canonical strategy schema](architecture/canonical-strategy-schema.md)).
- ✅ Canonical SHA-256 strategy fingerprints and verified immutable-dataset bindings.
- ✅ Bounded indicator registry: EMA, SMA, RSI, ATR, and volume SMA.
- ✅ Typed comparisons and bounded recursive AND/OR/NOT condition groups.
- 🚧 Reference EMA trend profile implemented as a backend contract; its template, authoring API, and
  browser workflow are the next user-visible increment.
- 🚧 Published versions are immutable; durable draft/archive lifecycle remains.
- 🚧 Human-readable strategy summaries remain.

**Exit gate:** the same immutable strategy version can be validated and associated with a
reproducible dataset snapshot.

**Declarative publication exit gate met:** the implemented indicator and recursive-condition
language can be validated, published, verified by fingerprint, and durably associated only with a
verified dataset fingerprint. Phase 2B remains in progress until the remaining policy variants,
lifecycle, summaries, and authoring surface are implemented.

## Phase 3: Backtesting

- ✅ Strict canonical immutable research-run specifications bind exact published strategy and verified
  dataset fingerprints to half-open evaluation/warmup ranges, exact capital/fee/slippage assumptions,
  completed-close/next-open timing, an explicit seed, and the implemented request-contract version.
- ✅ Append-only PostgreSQL publication requires the existing exact strategy/dataset binding and
  reverifies canonical bytes, denormalized row identity, immutable artifacts, coverage, and final
  next-open fill data on every load.
- ✅ Versioned deterministic indicator and entry-condition evaluation for executable
  `thytrader-bar-signal-v1` publications, with strict no-lookahead candle selection, canonical
  fingerprinted traces, and a read-only CLI. Traces are ephemeral and are not backtest results.
- ✅ `thytrader-bar-backtest-v1` event-driven long-only single-position simulation with private Decimal64
  arithmetic, strict no-lookahead signal boundaries, next-open marketable fills, ATR sizing, adverse
  fixed slippage, taker fees, initial-stop/take-profit/time-exit state, conservative same-bar stop-first
  ordering, forced final next-open liquidation, canonical trade/equity/drawdown/metrics output, append-only
  PostgreSQL results, and a read-only simulation CLI. See [backtest simulation](architecture/backtest-simulation.md).
- ✅ Read-only API and dashboard inspection for immutable result summaries, full reverified trade ledgers, equity curves, provenance, and disclosed simulation assumptions. The dashboard cannot submit simulations, mutate results, or grant trading authority.
- ✅ `thytrader-bar-backtest-v2` uses the same deterministic single-position event ordering with a canonical,
  disclosed constant-basis-point spread stress model: ask-side entries, bid-side exits and triggers,
  bid-close equity marking, executable-entry sizing, immutable fill-level evidence, and zero-spread
  economic regression to V1. V1 result bytes remain loadable/reverifiable unchanged; V2 is not
  observed order-book data or a live-fill prediction.
- Conservative bar-level broker with latency, rejection, partial-fill, and maker-limit models.
- Multi-position and cross-strategy portfolio/risk policy integration.
- Trailing-stop state machine when the schema and market-data resolution support it.
- Out-of-sample and walk-forward workflow.
- ✅ Deterministic versioned `thytrader-buy-and-hold-v1` benchmark comparison derived from the reverified result, source run, and immutable dataset. It uses the same published taker fee, fixed slippage, and V1/V2 fill assumptions, reports return/drawdown/cost evidence, preserves V1/V2 canonical bytes, and is exposed as a separate read-only API/dashboard comparison. See [derived buy-and-hold benchmark](decisions/0011-derived-buy-and-hold-benchmark.md).

### Next delivery increment

The current API and dashboard inspect immutable results only; they cannot publish a strategy or
submit a backtest. Before additional simulation fidelity work, expose the existing publication and
`evaluate_and_publish_backtest` application path through a narrow mutation API and the strategy UI.
Submission must require an already-published strategy fingerprint and a verified dataset fingerprint,
return immutable result identity/evidence, remain idempotent for equivalent inputs, and never create
paper or live trading authority.

**Exit gate:** reference-strategy results are deterministic, disclose assumptions, resist lookahead,
and pass adversarial fill/risk tests.

## Phase 4: Paper execution

- Persistent simulated broker using normalized 1h candle-close events for the first reference loop;
  live market events can expand the source later.
- Same published strategy semantics and independent risk path used by backtests/live trading.
- Continuous worker supervision.
- Restart recovery and reconciliation tests.
- Runtime monitoring, pause/resume, and kill switches.

**Exit gate:** a user can deploy one published reference strategy to paper mode in the UI; it evaluates
each eligible candle exactly once, records simulated intents/fills/position/P&L, obeys pause and kill
controls, and survives forced restarts and ambiguous events without duplicated orders or lost state.

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

Agent integration begins earlier as each supported interface becomes available; this phase completes
the full operational surface. It must not wait for live trading, and it must not give an agent trading
authority merely because it can inspect a system.

- Stable versioned read-only diagnostics API and CLI, starting with health, configuration validity,
  market-data quality, published-strategy state, and backtest evidence.
- Redacted health/configuration/data-quality/performance reports.
- In-repo ThyTrader operator skill.
- Machine-readable schemas and compatibility checks.
- Explicitly separated, confirmation-gated research mutation tools for drafts, immutable publication,
  and backtest submission if they prove useful. These are distinct from future paper/live authority.

**Exit gate:** an external agent can diagnose a running instance using supported interfaces
without database access, secret exposure, or implicit trading authority.
