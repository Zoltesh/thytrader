# 0084: Home: KPI tiles, Needs attention, and Data health from existing endpoints

- Status: Accepted
- Date: 2026-10-02
- Relates to: [0079](0079-four-destination-shell-agent-panel-palette-tokens.md),
  [0080](0080-per-strategy-workspace-build-test-run-why.md),
  [0081](0081-live-chrome-portfolio-bot-detail-trade.md),
  [0064](0064-deployment-http-lifecycle-and-breaker-latch-reset.md),
  [0065](0065-deployment-capital-accounting-http.md),
  [0072](0072-catalog-health-bounded-gaps-self-complete-ingest.md),
  [0073](0073-durable-research-jobs.md)

## Context

After [ADR 0081](0081-live-chrome-portfolio-bot-detail-trade.md), Home was the last page in the old
layout: a hero, a demo banner, Coinbase connection and permission cards, an asset table, the fee
tier, and a long market-data diagnostics panel. Everything waited on the slow Coinbase portfolio
call, and nothing told an operator what needed them: a paused bot, a tripped breaker, a stale
dataset, or a failed backtest each meant visiting another page.

Existing contracts shaped what Home can say:

- `GET /api/v1/portfolio/history` serves `24h`, `7d`, `30d`, and `all`, and thins a range longer
  than 300 rows to bucketed representative snapshots without saying so in the response.
- `GET /api/v1/research/jobs` lists jobs for one strategy at a time.
- `GET /api/v1/operator/data-catalog` can take about 20 seconds.

## Decision

Home follows the approved redesign: a header, four KPI tiles, the portfolio value chart beside
Needs attention, Your bots, Holdings, a fee tier line, and a Data health disclosure. **No backend
contract changes; no ops-contract bump.**

- **Independent loading.** Each source is its own client-side resource: the newest reload wins,
  and a failed reload keeps the last good value. Every card has its own skeleton and error with
  **Retry**. No card waits on the portfolio or the data catalog.
- **Header.** "Home", one line with the Coinbase connection, every detected permission, and the
  snapshot age, plus **New order** (`/trade`) and **New strategy** (`/strategies`). The demo banner,
  fresh-install onboarding, and the stale-snapshot disclosure stay.
- **KPI tiles.** Each figure comes from an existing endpoint. A figure that cannot be known is `—`
  with the reason:
  - *Portfolio value*: the newest of the Coinbase reading and the newest 24h snapshot. Its change
    runs from the oldest snapshot in the 24h window, or reads "since HH:MM" when history is shorter
    than a day. Demo totals are labelled demo and show no change.
  - *Available to trade*: the Coinbase `available` balance in the risk policy's `quote_currency`.
    *Reserved by live bots* sums `capital.allocated_capital` of running and paused live bots in that
    currency. ADR 0065 calls that field the strategy's risk-policy reservation.
  - *Live exposure*: Portfolio's gross marked exposure for running and paused live bots, per quote
    currency (unknown when an open book lacks a complete mark), the live bot count, and a protection
    summary.
  - *Bots*: running and paused counts, and the number of distinct bots in Needs attention.
- **Needs attention.** One list, aggregated in the browser from existing endpoints. Each item has
  an icon and a headline in words (never color alone), a severity read to screen readers, a LIVE
  tag for real money, and one link to where the problem is fixed:
  - From deployments: paused bots; bots reporting `mismatch_detail`; any other status; tripped
    daily-loss or drawdown latches; live books without confirmed exit cover. Stopped bots are
    skipped, except live ones that still report a mismatch or hold an unprotected book.
  - While live bots are running or paused: Coinbase credentials not configured; risk policy
    `source` not `published`.
  - Watched datasets in the data catalog whose latest attempt failed, that are stale, or that have
    gaps. Also backfills that are stuck: the scheduled attempt in `GET /api/v1/market-data/ingestion`
    is overdue by more than one interval (at least 15 minutes), an attempt has been running for
    over an hour, or the backfill keeps failing. At most 10 backfilling datasets are checked per
    load.
  - Research: the newest job of each of the 12 most recently updated strategies, when it failed
    within the last week.

  Critical items come first, then live ones. Sources that are loading, partly read, or failed are
  listed under the items with their own retry. "Nothing needs you right now" appears only after
  every source was read.
- **Chart.** `1D` / `1W` / `1M` / `3M` map onto `24h` / `7d` / `30d` / `all`. 3M keeps the last 90
  days of `all` in the browser. When a response reaches the 300-row cap, gap detection expects the
  median spacing between samples (never less than the snapshot cadence). Thinning is then not drawn
  as hundreds of gaps, while a real outage still is. The line is never interpolated.
- **Holdings.** A compact card: the ten largest balances by the current sort with **Show all**.
  The three-state sort and the dust summary are kept. A failed refresh keeps the last snapshot
  beside the redacted error.
- **Data health.** A disclosure at the bottom of Home, closed by default and opened by
  `/#data-health`. It lists watched datasets with the same problem words as Needs attention, and
  loads the per-product `MarketDataPanel` diagnostics only when it first opens. Dataset items with
  no strategy on that market and clock link here.

## Consequences

- An operator sees balances, live exposure, and everything that needs them on one screen. Each item
  links to the place where it is fixed.
- Home never totals simulated and real money, and never presents an unread source as clear.
- Coverage is bounded and disclosed. Research checks cover the 12 most recently updated strategies,
  because the jobs endpoint is per strategy. Stuck-backfill checks cover 10 datasets per load.
- The web keeps `HISTORY_RESPONSE_CAP = 300`, mirroring `_MAX_HISTORY_ENTRIES` in the history
  route; the two must change together.
- `app.css` declares its skeleton and spinner animations after its own reduced-motion rule, so that
  rule never wins. Home turns both animations off locally. The global ordering belongs to the shell.

## Alternatives considered

- **A new aggregated attention endpoint:** deferred. Composing in the browser proves the rules
  first, and agents already read the same facts from operator reports.
- **A System "Data health" page:** deferred. A disclosure keeps the diagnostics one click from Home
  without a new route, rail entry, or palette command.
- **Every backfilling dataset as an attention item:** rejected. A progressing backfill needs no
  action. Only one the worker is not advancing does.
- **The old strategy-performance strip (one PnL across bots):** dropped. ADR 0081 forbids totalling
  paper and live money; each bot's PnL is in Your bots.
