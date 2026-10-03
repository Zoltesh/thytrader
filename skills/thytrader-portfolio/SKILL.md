---
name: thytrader-portfolio
description: >-
  Create and edit ThyTrader portfolios (sleeves of strategies with capital
  weights, a cash reserve, shared limits, and manager settings), run portfolio
  backtests, read the journal, and act as the portfolio's manager agent: read
  the one-call briefing, then submit proposals (rebalance, pause or resume a
  sleeve, add a sleeve) with a rationale and cited evidence, through the
  confirmation-gated thytrader-portfolio CLI. Also records a person's
  approve/decline when the user explicitly decides. Requires --confirm on every
  mutation; YOLO never skips it. Cannot deploy (start/pause/resume/stop are
  thytrader-runtime portfolio-* commands) and never places orders.
---

# ThyTrader portfolios

A **portfolio** is a set of **sleeves** (one strategy each, with a capital weight) under shared
limits, run by you or by a **manager agent** that moves capital and pauses sleeves while the
strategies place every trade ([ADR 0088](../../docs/decisions/0088-portfolio-model-and-portfolio-backtest.md),
[ADR 0091](../../docs/decisions/0091-portfolio-deployment-limits-and-manager-proposals.md)).
A portfolio is `paper` or `live`, never mixed; mode and quote currency are fixed at creation.

This lane **cannot deploy** a portfolio and never places orders. Starting, pausing, resuming, and
stopping a portfolio (one bot per sleeve) and resetting its breaker are
`thytrader-runtime portfolio-*` commands ([runtime skill](../thytrader-runtime/SKILL.md)); single
bots stay with `thytrader-runtime`, strategies and single backtests with `thytrader-research`.
Read-only composition and deployment state for diagnosis is `thytrader-operator portfolios`.

HTTP-only against the loopback API. The CLI resolves its base URL from `--base-url`, then
`THYTRADER_API_BASE_URL`, then the `THYTRADER_API_HOST` / `THYTRADER_API_PORT` settings (the same
`.env` Compose reads; the default port is `8200`, but installs may override it, so never hard-code a
port). For raw `curl`, export `THYTRADER_API_BASE_URL` and call `"$THYTRADER_API_BASE_URL/api/v1/..."`.
There is no `--local` mode. Mutations send `Authorization: Bearer <installation-token>` automatically
([ADR 0070](../../docs/decisions/0070-mutation-cli-installation-auth.md)). Every command first
checks the `/health/ready` ops contract (`thytrader-ops-contract-v62`); a mismatch means a stale
Compose image — rebuild with `make run` only when the user asked or the CLI reports it.

Do not edit `src/`, Alembic, tests, or Compose to work around a failure; report it.

## Commands

Run from the repository root. Output is JSON. Decimals are strings: weights and fractions take at
most four places (`0.3333` = 33.33%), quote amounts at most eight.

| Task | Command |
|---|---|
| List portfolios | `uv run thytrader-portfolio list [--limit 50] [--cursor C]` |
| Show one (sleeves, issues, allocation, limits, manager, revision, `deployment_state`) | `uv run thytrader-portfolio show --portfolio-id ID` |
| Create (one revision, limits and manager included) | `uv run thytrader-portfolio create --name Core --mode paper\|live --capital-quote 1000 [--quote-currency USDC] [--cash-reserve-fraction 0.1] [--max-total-exposure-fraction F] [--max-per-asset-fraction F] [--daily-loss-quote Q] [--max-drawdown-fraction F] [--mandate TEXT \| --mandate-file PATH] [--may-rebalance yes\|no] [--max-weight-change-per-week 0.1] [--may-pause-sleeves yes\|no] [--may-propose-sleeves yes\|no] --confirm` |
| Create from a document, with optional sleeves | `uv run thytrader-portfolio create --file portfolio.json --confirm` (flags override fields; the `POST /api/v1/portfolios` body: `name`, `mode`, `quote_currency`, `capital_quote`, `cash_reserve_fraction`, `limits`, `manager`, optional `sleeves`) |
| Delete a portfolio (sleeves, journal, backtests; strategies are kept) | `uv run thytrader-portfolio delete --portfolio-id ID --revision N [--dry-run] --confirm` (`--dry-run` previews without `--confirm`) |
| Change settings, limits, manager | `uv run thytrader-portfolio update --portfolio-id ID --revision N [--name] [--capital-quote] [--cash-reserve-fraction] [--max-total-exposure-fraction] [--max-per-asset-fraction] [--daily-loss-quote Q \| --clear-daily-loss] [--max-drawdown-fraction F \| --clear-max-drawdown] [--mandate TEXT \| --mandate-file PATH] [--may-rebalance yes\|no] [--max-weight-change-per-week 0.1] [--may-pause-sleeves yes\|no] [--may-propose-sleeves yes\|no] --confirm` |
| Add a sleeve | `uv run thytrader-portfolio add-sleeve --portfolio-id ID --revision N --strategy-id SID --weight-fraction 0.25 [--note TEXT] --confirm` |
| Add many sleeves in one revision (all or none) | `uv run thytrader-portfolio add-sleeves --portfolio-id ID --revision N --file sleeves.json --confirm` (a JSON list of `{"strategy_id", "weight_fraction", "note"?}`, or `{"sleeves": [...]}`) |
| Remove a sleeve | `uv run thytrader-portfolio remove-sleeve --portfolio-id ID --revision N (--sleeve-id X \| --strategy-id SID) --confirm` |
| Replace every weight | `uv run thytrader-portfolio set-weights --portfolio-id ID --revision N --weight ID=0.4 --weight ID=0.3 [--cash-reserve-fraction 0.2] --confirm` (ID is a sleeve id or its strategy id; name every sleeve once) |
| Run a portfolio backtest | `uv run thytrader-portfolio backtest --portfolio-id ID --maker-fee-rate 0.004 --taker-fee-rate 0.006 --fixed-slippage-bps 5 [--spread-bps 10] [--revision N] [--evaluation-start ISO --evaluation-end ISO] [--datasets-file PATH] [--wait] --confirm` |
| Poll a job or show a result | `uv run thytrader-portfolio show-backtest --portfolio-id ID (--job-id J \| --result-fingerprint F) [--full-curve]` |
| List stored results and recent jobs | `uv run thytrader-portfolio list-backtests --portfolio-id ID [--limit 10]` |
| Read the journal | `uv run thytrader-portfolio journal --portfolio-id ID [--limit 50] [--cursor C]` |
| Deployment state (read-only) | `uv run thytrader-portfolio deployment --portfolio-id ID` |
| Paper vs live entry fills of sleeves with a twin (read-only) | `uv run thytrader-portfolio fill-comparisons --portfolio-id ID` |
| Manager briefing (read-only) | `uv run thytrader-portfolio briefing --portfolio-id ID [--decisions-per-sleeve 5] [--journal-limit 20]` |
| Propose a rebalance | `uv run thytrader-portfolio propose --portfolio-id ID --revision N --kind rebalance --weight ID=0.45 --weight ID=0.35 [--cash-reserve-fraction 0.2] (--rationale TEXT \| --rationale-file PATH) [--evidence KIND=REF ...] --confirm` |
| Propose pausing or resuming a sleeve | `uv run thytrader-portfolio propose --portfolio-id ID --revision N --kind pause_sleeve\|resume_sleeve --sleeve-id ID --rationale TEXT [--evidence KIND=REF ...] --confirm` |
| Propose a new sleeve | `uv run thytrader-portfolio propose --portfolio-id ID --revision N --kind add_sleeve --strategy-id SID --weight-fraction 0.1 [--note TEXT] --rationale TEXT [--evidence KIND=REF ...] --confirm` |
| List proposals | `uv run thytrader-portfolio proposals --portfolio-id ID [--status pending\|applied\|declined\|failed\|expired] [--limit 20] [--cursor C]` |
| Show one proposal | `uv run thytrader-portfolio show-proposal --portfolio-id ID --proposal-id P` |
| Record a person's approval | `uv run thytrader-portfolio approve --portfolio-id ID --proposal-id P [--note TEXT] [--i-understand-live] --confirm` |
| Record a person's decline | `uv run thytrader-portfolio decline --portfolio-id ID --proposal-id P [--note TEXT] --confirm` |

Creation starts at revision 1 without a supplied revision. Later edits need the current `revision`
from `show` (or the briefing). A stale one fails with
`portfolio_revision_conflict` (HTTP 409, `current_revision` in the detail): read again and
re-decide; never retry blindly. `--evidence` takes `KIND=REF`: `backtest_result`,
`portfolio_backtest`, or `study` with a `sha256:…` fingerprint; `decision` with a decision `ref`
exactly as the briefing prints it (`<deployment_id>/<product_id>@<bar_starts_at>`); or
`deployment` with a bot id. ThyTrader records what you cite; it does not re-run it.

## Create a complete portfolio

`create --file` sends one POST, creating the portfolio and all initial sleeves atomically at
**revision 1** ([ADR 0101](../../docs/decisions/0101-atomic-portfolio-creation-with-sleeves.md)).
Health advertises `portfolio_sleeve_operations: ["batch_add", "create_with_sleeves"]`.
Use existing strategy ids from `thytrader-research list-strategies` or `show-strategy`:

```json
{
  "name": "Core",
  "mode": "paper",
  "quote_currency": "USDC",
  "capital_quote": "1000",
  "cash_reserve_fraction": "0.2",
  "limits": {"max_per_asset_fraction": "0.6"},
  "manager": {"mandate": "Follow trends in the majors."},
  "sleeves": [
    {"strategy_id": "01a0f000-0000-7000-8000-000000000101", "weight_fraction": "0.5", "note": "BTC"},
    {"strategy_id": "01a0f000-0000-7000-8000-000000000102", "weight_fraction": "0.3"}
  ]
}
```

Save as `portfolio.json`, replace the example ids with your existing strategy ids, and run
`uv run thytrader-portfolio create --file portfolio.json --confirm`. Flags override settings in
the file. `sleeves` may be omitted or empty; otherwise at most 32 distinct strategies are allowed.
Each has `strategy_id`, positive `weight_fraction` at most 1, and optional `note` (280 characters).
Strategies must exist and have a readable market in the portfolio's quote currency. Weights plus
reserve must be at most 1. An invalid sleeve or database failure leaves **no portfolio, sleeves,
or journal entries**. Success appends `created` and one `sleeve_added` per sleeve at revision 1.
Saved invalid strategy drafts retain the existing sleeve issue behavior; check `show` before
backtesting or deploying. Creation changes definitions only, including when `mode` is `live`.
It never deploys, arms live trading, or places orders. Starting remains a separate runtime action.

## Acting as the manager agent

The manager loop runs **outside** ThyTrader: you (Hermes or Claude through this skill) are the
manager. Strategies place every trade; the manager only moves capital and pauses or resumes
sleeves, and every change goes through a proposal. Each cycle:

1. **Read** `briefing --portfolio-id ID`. It returns, in one call: the mandate and permissions with
   the rolling weekly budget (`permissions.weight_moved_this_week`, `weight_budget_remaining`,
   `rebalance_auto_applies`), the deployment `state`, run `performance` (equity, net PnL, daily
   PnL, drawdown), `breaker` and `exposure` against the caps, every sleeve with its bot (status,
   net PnL, return, drawdown, exposure, whether it runs the strategy's current rules), its newest
   portfolio-backtest evidence and `drawdown_vs_backtest` (1.5 = live drawdown 1.5 times the
   backtest's), and its recent per-bar decisions with citable `ref`s, the pending and recent
   proposals, the journal, and the disclosures.
2. **Decide** against the mandate. Holding is the default: propose only when the evidence moved.
   Typical triggers: a sleeve drawing down well past its backtest (`drawdown_vs_backtest` ≥ 1.5)
   or blocked entry after entry (`entry_blocked` decisions) → pause it; a paused sleeve whose
   evidence recovered → resume it; a sleeve beating its evidence while another lags → rebalance
   inside the budget; a validated strategy with backtest or study evidence and room in the
   allocation → add it (only with `may_propose_sleeves`).
3. **Propose** with `propose … --confirm`, naming the briefing's `revision`, a plain rationale
   (what you saw, why it matters, what you expect), and the evidence refs you used. Read the
   response: `status: applied` means it auto-applied inside the permissions; `pending` means it
   waits for a person and `approval_reason` says why.
4. **Never place orders.** There is no order proposal (any other kind is refused with "the manager
   never places orders"). Do not run `thytrader-runtime` start/resume, `place-order`, or
   `set-risk-policy`, and do not change limits, permissions, or the mandate on your own.
5. **Never decide your own proposals.** `approve` / `decline` record a person's decision: run them
   only when the user explicitly told you to, quoting their decision in `--note`. Approving a
   resume on a live portfolio also needs `--i-understand-live`, which you pass only when the user
   explicitly acknowledged live trading.
6. **Report** to the user what you proposed, whether it applied or waits, and what you cited.
   Do not re-propose a declined change without new evidence.

**Cadence.** Run a cycle once per bar of the slowest sleeve clock, but not more often than every
hour (for example every 4 hours for 1h and 4h sleeves, daily for 1d sleeves), right after any
`breaker_tripped` journal entry, and when the user asks. One proposal per sleeve per cycle; while
a proposal for a sleeve is pending, wait for it. Pending proposals expire after 7 days; at most 20
wait at once (`portfolio_proposal_limit`).

### Permission semantics

| Permission | Without approval | Otherwise |
|---|---|---|
| `may_rebalance` with `max_weight_change_per_week` | A **paper** rebalance applies at once when the weight it moves (the larger of total increases and total decreases) fits what is left of the rolling 7-day budget of auto-applied rebalances | Live rebalances always wait (they move real capital); over-budget or `may_rebalance` off → waits |
| `may_pause_sleeves` | Pausing a **running** sleeve applies at once (no new entries; exits and protection continue) | Off → waits |
| `may_propose_sleeves` | — (an added sleeve always waits; approving adds it but does not start it) | Off → `add_sleeve` is refused (`portfolio_proposal_not_permitted`) |
| — | — | Resuming a sleeve always waits for a person (live approval also needs `i_understand_live`) |

There is no "may place orders" permission and none can be added.

**Proposal states.** `pending` → `applied` (a person approved and the change was made) /
`declined` / `failed` (approved, but it no longer fits the portfolio; `failure_code` says why) /
`expired` (7 days unanswered); a proposal inside the permissions is `applied` at once with
`auto_applied: true`. Only `pending` proposals can be decided (`portfolio_proposal_not_pending`,
409). Submission refusals: `portfolio_sleeve_not_running` (pause), `portfolio_sleeve_not_paused`
(resume), `portfolio_breaker_latched` (resume while a breaker is latched — only a person resets
it), `portfolio_proposal_no_change`, `portfolio_weights_incomplete` (name every sleeve), plus the
composition rules below. Every step is journaled (`proposal_submitted`, `proposal_approved`,
`proposal_declined`, `proposal_failed`, and the change itself) with the actor (`manager` for you
and your auto-applied changes, `operator` for a person) and your rationale.

## Rules the API enforces

- Sleeve weights plus `cash_reserve_fraction` never exceed 1 (`portfolio_allocation_exceeded`).
- One sleeve per strategy (`portfolio_sleeve_exists`, 409); at most 32 sleeves (`portfolio_sleeve_limit`,
  422; for example 11 majors on two clocks is 22). `add-sleeves` applies the same checks to the
  whole batch and adds every sleeve or none, in one revision with one `sleeve_added` journal entry
  per sleeve.
- A sleeve's strategy must trade in the portfolio's quote currency (`portfolio_sleeve_quote_mismatch`)
  and have a readable market (`portfolio_sleeve_product_unknown`).
- `set-weights` must name every sleeve exactly once (`portfolio_weights_incomplete`).
- A deployed portfolio cannot be deleted (`portfolio_deployed`, 409) and a sleeve whose bot is
  running or paused cannot be removed (`portfolio_sleeve_deployed`, 409): stop it first.
- Mode and quote currency cannot change; the manager permissions have no order authority
  (an unknown `may_*` permission is refused).
- Unknown ids: `portfolio_not_found`, `portfolio_sleeve_not_found`, `portfolio_proposal_not_found`,
  `strategy_not_found`; no database: `portfolio_storage_unavailable` (503).

`show` flags drifted sleeves in `issues` (`strategy_invalid`, `quote_currency_mismatch`,
`product_unknown`): strategies stay editable after they join a portfolio. Fix the strategy with
`thytrader-research` or remove the sleeve. Deleting a strategy removes its sleeves; each removal
is journaled (`sleeve_removed`, actor `system`, reason `strategy_deleted`) and
`delete-strategy` reports `portfolio_sleeves`.

`allocation` reports allocated, reserve, and unallocated capital and the **largest single asset**
against `limits.max_per_asset_fraction` (a multi-product sleeve counts toward each of its assets).
On a deployed portfolio the limits bind: every sleeve entry must fit
`max_total_exposure_fraction × capital_quote` and `max_per_asset_fraction × capital_quote` across
the portfolio's bots (blocked entries show `PORTFOLIO_TOTAL_EXPOSURE_LIMIT` /
`PORTFOLIO_ASSET_EXPOSURE_LIMIT` in the decision timeline), and the optional `daily_loss_quote` and
`max_drawdown_fraction` stops pause every sleeve and latch (`PORTFOLIO_DAILY_LOSS_STOP` /
`PORTFOLIO_DRAWDOWN_STOP`) until an operator runs `thytrader-runtime portfolio-reset-breaker`.
Weights decide each sleeve's capital: a weight change on a deployed portfolio moves each sleeve
bot's allocated capital on the worker's next cycle (live and paper; a paper sleeve never sizes
beyond its own paper cash).

## Portfolio backtests

`backtest` returns HTTP 202 with the queued `job` and the plan (snapshot fingerprint, capital
slice, decision dataset per sleeve). Poll `show-backtest --job-id` until `status` is `completed`
(then `result_fingerprint` is set and the result is included), or `failed` / `expired` with
`error_message`. `--wait` polls for you. Portfolio backtests run in the `research-worker` service
([ADR 0092](../../docs/decisions/0092-research-worker-pool.md)) and share its
`THYTRADER_RESEARCH_WORKER_COUNT` workers (default 2) with backtests and studies. `queued` means
waiting for a free research worker; read the backlog from `uv run thytrader-operator health`
(`payload.research_workers.queue` and `portfolio_backtests`). A job whose worker crashed is
re-queued after its lease expires.

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
`POST /api/v1/portfolios/{id}/sleeves`, `POST /api/v1/portfolios/{id}/sleeves/batch` (body
`{revision, sleeves: [{strategy_id, weight_fraction, note?}]}`, ADR 0094),
`PATCH/DELETE /api/v1/portfolios/{id}/sleeves/{sleeve_id}`,
`PUT /api/v1/portfolios/{id}/weights`, `GET /api/v1/portfolios/{id}/journal`,
`POST/GET /api/v1/portfolios/{id}/backtests`, `GET /api/v1/portfolios/{id}/backtests/jobs[/{job_id}]`,
`GET /api/v1/portfolios/{id}/backtests/{result_fingerprint}?max_points=`,
`GET /api/v1/portfolios/{id}/deployment`, `GET /api/v1/portfolios/{id}/fill-comparisons`,
`GET /api/v1/portfolios/{id}/briefing`
(contract `thytrader-portfolio-briefing-v1`), `GET/POST /api/v1/portfolios/{id}/proposals`,
`GET /api/v1/portfolios/{id}/proposals/{proposal_id}`, and
`POST /api/v1/portfolios/{id}/proposals/{proposal_id}/approve|decline` (body `{note?,
i_understand_live?}`). Browser mutations also need CSRF; the Portfolio page (`/deployments`) uses
the same routes and its Manager tab shows proposals with Approve / Decline / Ask why.
`thytrader-portfolio delete` sends `DELETE ?revision=N` (ADR 0094); it is refused with
`portfolio_deployed` (409) while any sleeve runs or is paused, so stop the portfolio with
`thytrader-runtime portfolio-stop` first. Delete only a portfolio the user named.

`thytrader-portfolio deployment` and `thytrader-runtime portfolio-status` return `state` plus one
`sleeves[]` row per sleeve; each row's `deployment` field is that sleeve's bot
(`deployment_id`, `status`, `phase`, `position_state`, `exit_in_flight`, `lifecycle_command`,
`allocated_capital`, `net_pnl`, `return_fraction`, `drawdown_fraction`, `exposure_quote`,
`open_books`, `books[]`, `strategy_fingerprint`, `running_current_rules`) or `null` before the
sleeve is started. Each `books[]` row is one open book: `product_id`, `side`, `quantity`,
`entry_price`, `stop_price`, `target_price`, `entered_bar`, `position_state`, and `mark_price` /
`marked_at` / `unrealized_pnl` (the last evaluated bar's close from the decision journal, and gross
unrealized PnL before exit fees; null without a journaled close;
[ADR 0098](../../docs/decisions/0098-library-views-book-marks-portfolio-fills.md)).
`entry_fees` and `unrealized_pnl_net` add paid entry fees allocated to the held quantity and
gross PnL minus those fees. Prefer net when available; future exit fees are excluded. Partial
exits allocate entry fees proportionally; adds accumulate them. Null means no mark or unverified
fill evidence (including more than 1000 applied current-window fills), never zero fees
([ADR 0100](../../docs/decisions/0100-fee-adjusted-open-book-pnl.md)). Action
responses list books without marks; read `deployment` for marked books. Describe a sleeve's book by `position_state`
([ADR 0097](../../docs/decisions/0097-runtime-parity-and-observability.md)): `open_protected`
means open with its TP/SL (or stop) resting, even though `phase` reads `pending_exit`; only
`exiting` (`exit_in_flight: true`) means the bot is selling.

### Paper vs live fills

`thytrader-operator portfolios` (`GET /api/v1/operator/portfolios`) adds
`paper_live_fill_comparisons[]`. Each row compares an explicitly linked paper/live pair running the same strategy snapshot
(equal `strategy_fingerprint`, whether sleeves or standalone bots; ADR 0102). Shared rules alone
never infer a partner. Multiple saved pairs can share the same fingerprint. Select or remove a
partner with the separate `thytrader-runtime show-twin` / `link-twin` / `unlink-twin` commands
([runtime skill](../thytrader-runtime/SKILL.md)); this portfolio lane stays read-only for links.
It reports each side's `entries_rested`, `entries_filled`, `entries_expired`, `entries_rejected`,
`entries_working`, `average_fill_vs_limit_bps` (positive means worse than the limit), and
`average_seconds_to_fill` / `median_seconds_to_fill`. Paper fills only when a closed candle trades
through the limit, so its waits run to the fill bar's close. Live waits end at the Coinbase fill,
which is often seconds. Use the comparison to explain why a live sleeve entered and its paper twin
did not (or entered later). Do not treat the gap as a fault. The report is read-only.

For one portfolio, `uv run thytrader-portfolio fill-comparisons --portfolio-id UUID`
(`GET /api/v1/portfolios/{id}/fill-comparisons`) returns `{portfolio_id, comparisons[], warnings[]}`
with the same rows, limited to twins whose paper or live book is a sleeve bot of that portfolio
(`paper.portfolio_id` / `live.portfolio_id` say which), newest-linked first, at most 10. The Portfolio
page's Sleeves tab shows them as the "Paper vs live" panel
([ADR 0098](../../docs/decisions/0098-library-views-book-marks-portfolio-fills.md)).
