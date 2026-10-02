# Portfolios

Decision record: [ADR 0088](../decisions/0088-portfolio-model-and-portfolio-backtest.md). This page
maps the code and data flow; the ADR owns the semantics.

## Modules

| Module | Role |
|---|---|
| `thytrader.portfolios.models` | Domain records (`Portfolio`, `Sleeve`, `SleeveStrategy`, `JournalEntry`), validated decimal value types, request commands, errors |
| `thytrader.portfolios.rules` | Pure planning: every mutation becomes a `MutationPlan` (next row at revision + 1, full next sleeve set, journal entries); allocation summary |
| `thytrader.portfolios.store` | `PortfolioStore` / `PortfolioBacktestStore` protocols, in-memory (tests) and disabled (no database) stores |
| `thytrader.persistence.postgres_portfolio_rows` | In-transaction row mapping, plan application, and the strategy-deletion hook (no backtest imports, so `postgres_strategies` can call it) |
| `thytrader.persistence.postgres_portfolios` | `PostgresPortfolioStore`: transactions, row locks, revision-guarded writes, job queue, canonical results |
| `thytrader.portfolios.planning` | Snapshot sleeves, bind datasets, resolve and intersect windows (`backtest.submission.resolve_backtest_window`) |
| `thytrader.portfolios.jobs` | `PortfolioBacktestRunner` in the API process: run children through `BacktestSubmitter`, combine off the event loop |
| `thytrader.portfolios.combine` | Union grid, forward fill, drawdown, idle capital, correlation, overlap, equal-weight basket |
| `thytrader.portfolios.backtest` | Request, plan, result, listing, and job contracts; canonical bytes and fingerprint |
| `thytrader.portfolios.views` | HTTP response models shared by the routes and the CLI |
| `thytrader.api.routes.portfolios` | `/api/v1/portfolios` |
| `thytrader.portfolios.cli` / `client` | `thytrader-portfolio` lane |
| `thytrader.operator.portfolios_report` | Operator report kind `portfolios` |

## Tables (Alembic 0054)

`portfolios` (one row, `revision > 0`, mode/quote CHECKs), `portfolio_sleeves` (FK to portfolios
and strategies, both `ON DELETE CASCADE`; unique `(portfolio_id, strategy_id)`),
`portfolio_journal_entries` (append-only; `sequence` gives append order),
`portfolio_backtest_jobs` (queue; plan JSON payload), `published_portfolio_backtests` (canonical
result JSON plus a listing row). Decimals are canonical text with format CHECKs.

## Mutation flow

```mermaid
sequenceDiagram
  participant Client as Browser / CLI
  participant API as /api/v1/portfolios
  participant Store as PostgresPortfolioStore
  participant DB as PostgreSQL
  Client->>API: PUT /weights {revision, weights}
  API->>Store: set_weights(request, context)
  Store->>DB: BEGIN; SELECT portfolio FOR UPDATE; sleeves + strategy facts
  Store->>Store: plan_set_weights (revision check, weights + reserve <= 1)
  Store->>DB: UPDATE portfolio WHERE revision = N; sleeve diff; INSERT journal; COMMIT
  API-->>Client: portfolio at revision N + 1 (or 409 / 422)
```

Adding a sleeve locks the strategy row (`FOR SHARE`) before the portfolio row; strategy deletion
locks the strategy row (`FOR UPDATE`) and then each portfolio in `portfolio_id` order, journals
`sleeve_removed`, and only then deletes the strategy.

## Backtest flow

`POST /backtests` plans synchronously (422 `portfolio_backtest_rejected` on any sleeve problem)
and queues the plan. The runner claims one job at a time, submits each dated child request
(published and deduplicated like any backtest), reloads the verified child results, loads the
basket candles from the finest-clock sleeve per primary product, combines, stores the canonical
result, journals `backtest_run`, and completes the job. Restarted APIs requeue running jobs; jobs
expire after 24 hours.
