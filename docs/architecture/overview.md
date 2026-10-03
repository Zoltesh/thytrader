# Architecture Overview

Mode-wide live exposure and daily-loss capital is observed account quote plus managed long
inventory cost and working buy-entry reservations; bot allocations are separate sizing limits.
Do not substitute one sleeve's allocation or add its ledger cash to an account balance.
[ADR 0106](../decisions/0106-account-risk-capital-and-live-startup-baselines.md) corrects this scope
and initializes new live strategy fill-ledger baselines at zero before worker supervision.

Performance percentages use separate pinned capital while ledger cash/equity keep their dollar
PnL meaning ([ADR 0107](../decisions/0107-capital-normalized-live-performance.md)). The shared
execution ledger normalizes return and drawdown for HTTP, operator, and portfolio readers.
Workers persist the budget and maximum observed drawdown independently of the ledger peak;
restart and rebalance retain both. Alembic `0061` adds nullable columns without historical
backfill; ops contract v64 exposes their HTTP fields and capability.

## System shape

ThyTrader is a modular monolith deployed as multiple supervised processes. Domain packages share one
repository and release lifecycle, while API and worker processes provide fault and scaling boundaries.

The **primary product surface is agent-driven E2E** (versioned HTTP + confirmation-gated skills;
[ADR 0030](../decisions/0030-agent-e2e-primary-surface.md)). The SvelteKit UI remains a required
professional workstation. Product destination is a Coinbase-first research and trading platform
([ADR 0031](../decisions/0031-coinbase-first-platform-end-state.md)); other exchanges come later.

The diagram describes the **target system shape**, not a claim that every responsibility is already
implemented. Today, the browser, HTTP API, and agent CLIs provide portfolio, market-data, strategy
authoring, backtests, and paper/live deployments of a venue-clock strategy snapshot.
The portfolio worker takes snapshots; the market-data worker maintains verified 1h, 5m, 15m, 30m,
6h, 1d, 1m, 2h, and 4h datasets; the execution worker evaluates closed venue candles and submits maker orders
through a paper broker or Coinbase Advanced Trade REST v3. Paper and live entries pass the
`thytrader-risk-policy-v1` registry before intent persist.

```text
SvelteKit web UI
Agent skills / CLIs  (primary product surface)
      |
      | REST + ThyTrader WebSocket
      v
FastAPI API process ---------------- PostgreSQL
      |                                  |
Portfolio worker ------------------------+
Market-data worker ----------------------+
Execution worker ------------------------+
Research worker (N processes) -----------+   backtests, studies, portfolio backtests
      |
      +---- Coinbase market-data REST
      +---- Coinbase Advanced Trade REST v3 orders (live only)
      +---- immutable Parquet datasets <----> Polars / DuckDB
```

## Initial components

### Web application

- SvelteKit, Svelte 5, and strict TypeScript ([ADR 0001](../decisions/0001-sveltekit-frontend.md)).
- Desktop-first responsive interface. Shared workstation chrome lives in the root layout, so route
  pages render body content only (a compact `PageHead` plus the page)
  ([ADR 0079](../decisions/0079-four-destination-shell-agent-panel-palette-tokens.md)):
  - A left rail with four destinations (Home `/`, Strategies `/strategies`, Portfolio
    `/deployments`, Trade `/trade`) and a collapsible System group (Settings, Audit log, Journal,
    Memory & why-trade). The rail collapses to icons on narrow desktop widths.
  - A per-strategy workspace layout at `/strategies/[id]` with stage routes Build (`/`), Test
    (`/test`), Run (`/run`), and Why (`/why`), and a sticky identity bar with the strategy's
    saved/validation state, shared through Svelte context (`web/src/lib/workspace/`)
    ([ADR 0080](../decisions/0080-per-strategy-workspace-build-test-run-why.md),
    [ADR 0082](../decisions/0082-strategy-root-mutable-strategies-auto-snapshots.md)). Build saves in
    place; results and bots show Current rules / Earlier edit against `current_fingerprint`.
    `/research`, `/deploy`, old `?version=` links, and fingerprint deep links resolve the owning
    strategy and redirect; `/backtests` resolves a result's owning strategy client-side. Pure view
    logic (snapshot comparison, library pipeline, live preflight, signal wording) lives in
    `web/src/lib/strategy-workspace.ts` and composes existing endpoints only.
  - A top bar with a breadcrumb, a ⌘K / Ctrl+K command palette (navigation only), an Agent toggle,
    and a theme toggle.
  - Global live chrome: a route that shows live exposure or composes a live order declares a live
    context (`declareLiveContext()` in `web/src/lib/live-context.svelte.ts`, released from its
    `$effect` cleanup). The shell then renders an amber `LIVE:` strip (icon plus text, in a polite
    live region) under the top bar and an inset frame around the main column. Used by live bot
    detail, Trade in Live mode, and the Run stage while the live-arm dialog is open.
  - Portfolio (`/deployments`) and bot detail (`/deployments/[id]`): paged inventory rows grouped
    Needs attention / Running / Paused / Stopped with an All / Paper / Live filter; header metrics
    from `web/src/lib/deployment-portfolio.ts` (never totalled across paper and live or across
    quote currencies; `—` when not computable). Bot detail composes header controls, four KPI cards,
    an Orders / Fills switch over the cursor-paged ledgers, and a shared `TradeReasonTimeline`
    (also used by the Why stage). Lifecycle dialogs share the native `ConfirmDialog` shell.
  - Trade (`/trade`): ticket plus a Review aside whose entry, loss-at-stop, and reward:risk use exact
    rational arithmetic (`web/src/lib/trade-review.ts`); live submits go through a `ConfirmDialog`
    with the real-orders checkbox.
  - An Agent side panel hosting `OperatorChatPanel`, the same component as `/chat`, on every route.
  - Design tokens: CSS custom properties in `web/src/app.css` for `:root` (dark) and
    `[data-theme='light']`. An inline boot script in `app.html` sets the theme before first paint.
    Components use tokens, never raw hex. Geist / Geist Mono are bundled locally.
- The UI is required and must stay capable; it is **not** the completeness bar for a new
  capability ([ADR 0030](../decisions/0030-agent-e2e-primary-surface.md)).
- TradingView Lightweight Charts (canvas) renders portfolio history and backtest equity. Portfolio gaps stay visible on a wall-clock time scale with no Y interpolation; backtest equity is labeled as mark-to-model research evidence. Market-data diagnostics stay non-charted.
- Backtest discovery discloses its newest-first page bound and deep-links an open immutable result with `?result=` (inline on the workspace Test stage, or standalone on `/backtests`). The audit trail UI labels its latest-50 bound so the page is not read as a complete archive.
- The browser never receives exchange secrets.
- Typed clients should be generated from FastAPI's OpenAPI contract where practical.

### API process

FastAPI owns the supported application interface. Its implemented surface is portfolio, portfolio-history,
market-data, worker-state, health, immutable backtest-result retrieval, and the bounded research mutation
contracts below:

- `GET /api/v1/strategies` pages the strategy library; `POST /api/v1/strategies` creates a strategy
  from a template with a server-owned identity; `GET` / `PUT` / `DELETE
  /api/v1/strategies/{strategy_id}` read, save (revision-guarded; invalid documents allowed with
  their validation), and hard-delete one strategy; `POST /api/v1/strategies/bulk-delete`,
  `POST /api/v1/strategies/{strategy_id}/clone`, and `POST /api/v1/strategies/import` cover bulk
  delete, clone, and import; `GET /api/v1/strategies/snapshots/{strategy_fingerprint}` reads one
  snapshot ([ADR 0082](../decisions/0082-strategy-root-mutable-strategies-auto-snapshots.md));
- `POST /api/v1/backtests` takes a `strategy_id`, snapshots the current definition, binds a verified dataset, publishes/reuses the exact research run, and invokes
  the single deterministic backtest model (`engine: "thytrader-backtest"`,
  [ADR 0083](../decisions/0083-unified-backtest-model.md));
- `GET /api/v1/research/backtest-model`, `GET /api/v1/research/templates`, `POST /api/v1/research/studies/plan`,
  `POST /api/v1/research/studies`, `GET /api/v1/research/studies`, and
  `GET /api/v1/research/studies/{study_fingerprint}` compose walk-forward / OOS / cross-market /
  sweep / WFO studies from that model and persist catalog rows
  ([research studies](research-studies.md));
- `/api/v1/portfolios` composes portfolios (sleeves with capital weights, cash reserve, shared
  limits, manager settings, an append-only journal), runs async portfolio backtests that combine
  independently simulated sleeves, deploys a portfolio as one bot per sleeve (start, pause,
  resume, stop, breaker reset), and carries the manager loop (proposals and the briefing); the
  execution worker supervises deployed portfolios each cycle and the entry gate applies their caps
  and latched breakers ([portfolios](portfolios.md), [ADR 0088](../decisions/0088-portfolio-model-and-portfolio-backtest.md),
  [ADR 0091](../decisions/0091-portfolio-deployment-limits-and-manager-proposals.md));
- `POST /api/v1/deployments` starts a paper or live runtime for one `strategy_id` from a snapshot of its current definition; pause, resume,
  and stop are explicit subsequent calls. Create and closed-bar entries evaluate the risk-policy
  registry before persisting a new intent.
- `GET` / `PUT /api/v1/risk-policy` reads or publishes the effective `thytrader-risk-policy-v1`
  document. `PUT` requires durable PostgreSQL storage.
- `GET` / `PUT` / `DELETE /api/v1/credentials/coinbase` is write-only Coinbase Advanced Trade
  presence ([ADR 0053](../decisions/0053-workstation-ia-write-only-coinbase-credentials.md)). GET
  never returns secrets. Setting credentials does not arm live trading.
- `GET /api/v1/operator/*` is the versioned read-only agent/operator diagnostics contract; the matching
  CLI is `thytrader-operator`. Research mutations for agents use `thytrader-research` with `--confirm`.
  In-app operator chat is `/api/v1/operator-chat` plus `/chat`
  ([ADR 0051](../decisions/0051-in-app-operator-chat.md)); it uses those same skill routes and is
  not Coinbase credential storage.
- `GET` / `PUT /api/v1/settings` is YAML non-secret settings including YOLO
  ([ADR 0055](../decisions/0055-yaml-settings-runtime-reloadable-yolo.md)). Secrets stay out.
  Bind address and dataset root remain env-at-boot.

A strategy is one mutable PostgreSQL row (`strategies`) guarded by a monotonically increasing
revision, so a stale browser cannot overwrite a newer save. Starts write content-addressed,
deduplicated `strategy_snapshots` rows; snapshots are never mutated. Bindings, run specs, results,
studies (plus the `research_study_strategies` link table), and research jobs reference
`strategies.strategy_id` with `ON DELETE CASCADE`; `deployments.strategy_id` uses `ON DELETE SET
NULL` and `deployments.strategy_name` is captured at start. Deleting a strategy is refused while any
of its deployments runs or is paused; it removes research evidence and paper books in one
transaction and keeps stopped live books (orders, fills, positions, trade reasons, snapshot) detached.

Paper and live share one execution worker and the same snapshotted strategy semantics. Live mode is the
arming action and requires Coinbase credentials; demo mode can paper-trade only. Coinbase order JSON
from Advanced Trade REST v3 is the live ledger. Phase 13 shipped 5m live, trailing stops, native
brackets/OCO, and user-order WebSockets ([ADR 0036](../decisions/0036-phase-13-live-extras.md)).
On-demand discretionary orders are shipped
([ADR 0039](../decisions/0039-on-demand-discretionary-trades.md)).
The Phase 10 risk-policy registry is shipped ([ADR 0033](../decisions/0033-phase-10-risk-policy-registry.md));
the full destination control catalog in [security-and-risk.md](../security-and-risk.md) is not.
Every ingested venue granularity is a legal strategy, paper, live, discretionary, and HTF clock
([ADR 0040](../decisions/0040-venue-strategy-paper-live-htf-clocks.md)). Paper and live evaluate
`htf_filter` on last-completed complete-only HTF bars
([ADR 0041](../decisions/0041-paper-live-htf-filter-evaluation.md)). Optional per-indicator
timeframes overlay last-completed extra-TF bars onto the decision clock
([ADR 0042](../decisions/0042-per-indicator-timeframes.md)).

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
execution run in `thytrader-execution-worker`, which polls closed venue candles over REST, evaluates
the active risk policy before new entries, and talks to a paper broker or the Coinbase REST v3 adapter. Pause continues synthetic stop/time-exit handling and
fill matching but blocks new entries; stop cancels resting orders. The worker replays contiguous
missed closed bars after downtime and pauses when the latest bar is missing or gapped. Sub-hour live
pauses unless the authenticated user-order feed is connected. ATR trailing is durable on the
position; live exits after fill are one Coinbase `trigger_bracket_gtc` OCO.
Every bar it processes for a strategy bot is journaled as one `thytrader-bar-decision-v1` row
(`bar_decisions`): outcome, reason, rule values versus thresholds, risk verdict, and linked orders.
Journaling is bounded in time, never blocks or changes trading, and is pruned by the worker
([ADR 0087](../decisions/0087-per-bar-decision-timeline.md)).

Market-data ingestion is already split into its own supervised process so its filesystem publication,
provider failures, and retry loop cannot overlap the portfolio-history worker. This is an operational
boundary within the modular monolith, not a microservice or trading-authority boundary.

### Research worker

Research compute never runs in the API process
([ADR 0092](../decisions/0092-research-worker-pool.md)). `thytrader-research-worker` (Compose
service `research-worker`) is a light supervisor that keeps `THYTRADER_RESEARCH_WORKER_COUNT`
worker processes alive (default 2). Each process claims one queued row at a time from
`research_jobs` (backtests, studies) or `portfolio_backtest_jobs` with `FOR UPDATE SKIP LOCKED`,
holds it under a lease its heartbeat thread renews, and writes progress and outcomes fenced by
that lease. A worker that dies leaves an expired lease; its row is re-queued (or failed as
`research_worker_lost` after the attempt limit). Processes recycle after a job count or RSS
growth. The API validates, plans, queues, and long-polls: a synchronous submit answers 201 when
the worker finishes within `THYTRADER_RESEARCH_SYNC_WAIT_SECONDS`, otherwise 202 with the job.
The worker has no Coinbase credentials and reads datasets read-only. Operator health reports
worker liveness, per-worker RSS, and queue depth.

### Storage

- **PostgreSQL:** configurations, strategies and their snapshots, runtime state, orders, fills, positions, risk state, jobs, and audit records.
- **Parquet:** immutable or append-oriented historical market datasets, partitioned by provider/product/timeframe/date as appropriate.
- **Polars:** primary dataframe/query engine in Python.
- **DuckDB:** ad hoc analytical SQL over Parquet and derived datasets.

Operational correctness must not depend on DuckDB or a dataframe remaining resident in memory.

## Domain boundaries

Expected durable boundaries include:

- `exchanges`: provider-neutral account, market-data, and broker interfaces;
- `market_data`: normalized products, candles, trades, ingestion, and quality checks;
- `strategies`: schemas, indicators, conditions, signals, mutable strategy storage, and snapshots;
- `backtesting`: clocks, events, fills, metrics, and reproducibility;
- `execution`: order intents, lifecycle, idempotency, and reconciliation;
- `risk`: composable pre-trade and runtime policies;
- `portfolio`: balances, positions, valuation, and exposure;
- `observability`: health, metrics, structured logs, and audit events.

Dependencies should point toward stable domain abstractions. Coinbase-specific response objects must not leak throughout the system.

Mermaid diagrams of the shipped contracts live under
[architecture/contracts](contracts/README.md).

## Portability and deployment

### Development

- Python dependencies and commands through `uv`.
- SvelteKit through a pinned Node package manager and lockfile.
- Native processes for fast iteration.

### Supported installation

Docker Compose should provide:

- web, API, portfolio worker, market-data worker, execution worker, research worker, and
  PostgreSQL services;
- health checks and restart policies;
- migrations before service readiness;
- persistent volumes for PostgreSQL, Parquet, and required application state;
- an `.env.example` containing names and placeholders only;
- a setup command that validates configuration before startup.

Default services bind to loopback. Remote access is an explicit deployment profile, not an accidental side effect.

## Evolution to Rust

Rust is a future implementation option for measured hot paths such as feed handling, event processing, order-book simulation, or execution components. Extraction should occur only after profiling shows a material benefit. Stable message/domain contracts make that evolution possible; speculative microservices do not.

Internet-connected Coinbase trading should not be marketed as true HFT merely because a component is written in Rust. Exchange and network latency, data quality, execution design, and risk controls dominate.
