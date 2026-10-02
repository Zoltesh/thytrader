---
name: thytrader-portfolio
description: >-
  Create and edit ThyTrader portfolios (sleeves of strategies with capital
  weights, a cash reserve, shared limits, and manager settings), run portfolio
  backtests, and read the portfolio journal through the confirmation-gated
  thytrader-portfolio CLI. Use when the user asks to build, change, compare,
  or backtest a portfolio. Requires --confirm on every mutation; YOLO never
  skips it. Cannot deploy a portfolio, paper-trade, live-trade, or place orders.
---

# ThyTrader portfolios

A **portfolio** is a set of **sleeves** (one strategy each, with a capital weight) under shared
limits, optionally run later by a **manager agent** that moves capital and pauses sleeves while the
strategies place every trade ([ADR 0088](../../docs/decisions/0088-portfolio-model-and-portfolio-backtest.md)).
A portfolio is `paper` or `live`, never mixed; mode and quote currency are fixed at creation.

This lane **cannot deploy** a portfolio, start paper or live trading, or place orders: deploying a
portfolio is not shipped (every response carries `deployable: false`). Run bots with
`thytrader-runtime`; strategies and single backtests stay with `thytrader-research`. Read-only
composition for diagnosis is `thytrader-operator portfolios`.

HTTP-only against the loopback API. The CLI resolves its base URL from `--base-url`, then
`THYTRADER_API_BASE_URL`, then the `THYTRADER_API_HOST` / `THYTRADER_API_PORT` settings (the same
`.env` Compose reads; the default port is `8200`, but installs may override it, so never hard-code a
port). For raw `curl`, export `THYTRADER_API_BASE_URL` and call `"$THYTRADER_API_BASE_URL/api/v1/..."`.
There is no `--local` mode. Mutations send `Authorization: Bearer <installation-token>` automatically
([ADR 0070](../../docs/decisions/0070-mutation-cli-installation-auth.md)). Every command first
checks the `/health/ready` ops contract (`thytrader-ops-contract-v50`); a mismatch means a stale
Compose image — rebuild with `make run` only when the user asked or the CLI reports it.

Do not edit `src/`, Alembic, tests, or Compose to work around a failure; report it.

## Commands

Run from the repository root. Output is JSON. Decimals are strings: weights and fractions take at
most four places (`0.3333` = 33.33%), quote amounts at most eight.

| Task | Command |
|---|---|
| List portfolios | `uv run thytrader-portfolio list [--limit 50] [--cursor C]` |
| Show one (sleeves, issues, allocation, limits, manager, revision) | `uv run thytrader-portfolio show --portfolio-id ID` |
| Create | `uv run thytrader-portfolio create --name Core --mode paper\|live --capital-quote 1000 [--quote-currency USDC] [--cash-reserve-fraction 0.1] --confirm` |
| Change settings, limits, manager | `uv run thytrader-portfolio update --portfolio-id ID --revision N [--name] [--capital-quote] [--cash-reserve-fraction] [--max-total-exposure-fraction] [--max-per-asset-fraction] [--daily-loss-quote Q \| --clear-daily-loss] [--max-drawdown-fraction F \| --clear-max-drawdown] [--mandate TEXT \| --mandate-file PATH] [--may-rebalance yes\|no] [--max-weight-change-per-week 0.1] [--may-pause-sleeves yes\|no] [--may-propose-sleeves yes\|no] --confirm` |
| Add a sleeve | `uv run thytrader-portfolio add-sleeve --portfolio-id ID --revision N --strategy-id SID --weight-fraction 0.25 [--note TEXT] --confirm` |
| Remove a sleeve | `uv run thytrader-portfolio remove-sleeve --portfolio-id ID --revision N (--sleeve-id X \| --strategy-id SID) --confirm` |
| Replace every weight | `uv run thytrader-portfolio set-weights --portfolio-id ID --revision N --weight ID=0.4 --weight ID=0.3 [--cash-reserve-fraction 0.2] --confirm` (ID is a sleeve id or its strategy id; name every sleeve once) |
| Run a portfolio backtest | `uv run thytrader-portfolio backtest --portfolio-id ID --maker-fee-rate 0.004 --taker-fee-rate 0.006 --fixed-slippage-bps 5 [--spread-bps 10] [--revision N] [--evaluation-start ISO --evaluation-end ISO] [--datasets-file PATH] [--wait] --confirm` |
| Poll a job or show a result | `uv run thytrader-portfolio show-backtest --portfolio-id ID (--job-id J \| --result-fingerprint F) [--full-curve]` |
| List stored results and recent jobs | `uv run thytrader-portfolio list-backtests --portfolio-id ID [--limit 10]` |
| Read the journal | `uv run thytrader-portfolio journal --portfolio-id ID [--limit 50] [--cursor C]` |

Every mutation needs the current `revision` from `show`. A stale one fails with
`portfolio_revision_conflict` (HTTP 409, `current_revision` in the detail): run `show` again and
re-decide; never retry blindly.

## Rules the API enforces

- Sleeve weights plus `cash_reserve_fraction` never exceed 1 (`portfolio_allocation_exceeded`).
- One sleeve per strategy (`portfolio_sleeve_exists`, 409); at most 20 sleeves (`portfolio_sleeve_limit`).
- A sleeve's strategy must trade in the portfolio's quote currency (`portfolio_sleeve_quote_mismatch`)
  and have a readable market (`portfolio_sleeve_product_unknown`).
- `set-weights` must name every sleeve exactly once (`portfolio_weights_incomplete`).
- Mode and quote currency cannot change; the manager permissions have no order authority
  (an unknown `may_*` permission is refused).
- Unknown ids: `portfolio_not_found`, `portfolio_sleeve_not_found`, `strategy_not_found`; no
  database: `portfolio_storage_unavailable` (503).

`show` flags drifted sleeves in `issues` (`strategy_invalid`, `quote_currency_mismatch`,
`product_unknown`): strategies stay editable after they join a portfolio. Fix the strategy with
`thytrader-research` or remove the sleeve. Deleting a strategy removes its sleeves; each removal
is journaled (`sleeve_removed`, actor `system`, reason `strategy_deleted`) and
`delete-strategy` reports `portfolio_sleeves`.

`allocation` reports allocated, reserve, and unallocated capital and the **largest single asset**
against `limits.max_per_asset_fraction` (a multi-product sleeve counts toward each of its assets).
Limits are stored now and start binding orders only when portfolio deployment ships. Manager
settings are stored and shown; no manager agent acts on them yet.

## Portfolio backtests

`backtest` returns HTTP 202 with the queued `job` and the plan (snapshot fingerprint, capital
slice, decision dataset per sleeve). Poll `show-backtest --job-id` until `status` is `completed`
(then `result_fingerprint` is set and the result is included), or `failed` / `expired` with
`error_message`. `--wait` polls for you. One portfolio backtest runs at a time; queued jobs wait.

Accepting the request resolves everything up front, so problems come back immediately as
`portfolio_backtest_rejected` (422) with `problems[]` (`code`, `message`, `sleeve_id`,
`strategy_id`, `strategy_name`):

- `strategy_invalid` / `strategy_not_found` — fix or remove the sleeve.
- `dataset_missing` — the message names the product and clock (decision, HTF filter, indicator
  clock, additional instrument). Ingest with `thytrader-data`, or pass `--datasets-file`: a JSON
  list of `{strategy_id, dataset_fingerprint, htf_dataset_fingerprint?, indicator_dataset_fingerprints?, additional_instrument_datasets?}`
  objects, exactly as a single backtest request would bind them.
- `window_rejected` / `no_common_window` — the sleeves' coverage does not overlap (each problem
  states that sleeve's usable window), or a supplied window does not fit a sleeve.

Semantics (contract `thytrader-portfolio-backtest-v1`): each sleeve runs the unified backtest
model with `capital = weight × capital_quote` over **one common window** (the intersection of
every sleeve's usable coverage, aligned to the coarsest sleeve clock); results are combined on
the union of the sleeves' bar closes with forward-filled sleeve equity plus reserve cash. The
result has the combined `equity_curve` (thinned to 200 points unless `--full-curve`), `summary`
(total return, max drawdown, idle capital, cash, best sleeve), `metrics` (Sharpe, Sortino, CAGR,
Calmar, volatility; rf 0), per-sleeve `contribution_fraction` (sums to the total return) and
standalone return, pairwise `correlation` plus `correlation_to_rest`, `overlap`
(`same_asset_fraction`, `long_together_fraction`), the equal-weight `basket` buy-and-hold, every
child `run_fingerprint`/`result_fingerprint`, and `disclosures`. State the disclosure when you
report numbers: sleeves are simulated independently on fixed capital slices; portfolio-level caps
and cross-sleeve interactions are **not simulated**. Fills are simulated from candles.

## HTTP

`GET/POST /api/v1/portfolios`, `GET/PATCH/DELETE /api/v1/portfolios/{id}` (`DELETE ?revision=N`),
`POST /api/v1/portfolios/{id}/sleeves`, `PATCH/DELETE /api/v1/portfolios/{id}/sleeves/{sleeve_id}`,
`PUT /api/v1/portfolios/{id}/weights`, `GET /api/v1/portfolios/{id}/journal`,
`POST/GET /api/v1/portfolios/{id}/backtests`, `GET /api/v1/portfolios/{id}/backtests/jobs[/{job_id}]`,
`GET /api/v1/portfolios/{id}/backtests/{result_fingerprint}?max_points=`. Browser mutations also
need CSRF; the Portfolio page (`/deployments`) uses the same routes. Deleting a portfolio has no
CLI command on purpose; it needs the browser or an explicit HTTP call with the current revision.
