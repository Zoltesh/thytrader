---
name: thytrader-runtime
description: >-
  Start, pause, resume, or stop ThyTrader paper and live deployments and
  whole portfolios (portfolio-start/pause/resume/stop, one bot per sleeve, and
  portfolio-reset-breaker), read their per-bar decision timeline (read-only
  `decisions`), publish
  the risk-policy registry, and show/set/clear write-only Coinbase credentials,
  through the confirmation-gated thytrader-runtime CLI. Use when the user
  explicitly asks to deploy, pause, resume, stop, place an on-demand order, set
  the risk policy, or manage Coinbase API secrets. Requires --confirm on every
  mutation unless YOLO covers that tier. Live start, live resume, and live
  place-order also require --i-understand-live (sent as HTTP i_understand_live=true).
  YOLO live may skip --confirm on start/pause/resume/stop
  only. Credential set/clear always need --confirm; YOLO never covers them.
  Publishing a risk policy or setting credentials does not arm live trading.
  Never diagnose through this skill and never submit Coinbase orders directly.
---

# ThyTrader runtime

Confirmation-gated paper and live **control**, including the risk-policy registry and write-only
Coinbase Advanced Trade credentials. This skill is not an extension of `thytrader-operator` or
`thytrader-research`.

HTTP-only against the loopback API. The CLI resolves its base URL from `--base-url`, then `THYTRADER_API_BASE_URL`, then the
`THYTRADER_API_HOST` / `THYTRADER_API_PORT` settings (the same `.env` Compose reads; the default
port is `8200`, but installs may override it, so never hard-code a port). For raw `curl`, export
`THYTRADER_API_BASE_URL` and call `"$THYTRADER_API_BASE_URL/api/v1/..."`. There is no `--local` database mode.

Production installs enforce the application trust boundary
([ADR 0061](../../docs/decisions/0061-application-trust-boundary.md)): HTTP mutations need
`Authorization: Bearer <installation-token>` from `THYTRADER_INSTALLATION_TOKEN` or
`$THYTRADER_CREDENTIALS_DIR/.installation-token` ([ADR 0070](../../docs/decisions/0070-mutation-cli-installation-auth.md)
documents the shared helper used by every mutation lane). Browser mutations additionally require CSRF
from `GET /api/v1/security/session`. Live arming still requires a published risk policy per
[ADR 0063](../../docs/decisions/0063-stage-5-release-discipline-ci-risk-defaults-rate-budget.md)
plus `--i-understand-live`; do not expect a separate live-arm token endpoint. Over HTTP the
same acknowledgement is the strict boolean `i_understand_live: true` on `POST /api/v1/deployments`
(mode `live`), `POST /api/v1/deployments/{id}/resume` (live books), and
`POST /api/v1/discretionary-orders` (mode `live`); without it the API answers **HTTP 428** with
detail `live_acknowledgement_required: …` and nothing is created or resumed
([ADR 0078](../../docs/decisions/0078-live-readiness-http-ack-venue-reload-definite-rejects.md)).
In the browser these controls live on each strategy's Run stage (`/strategies/{strategy_id}/run`;
old `/deploy?strategy=` and `?version=` links redirect there,
[ADR 0080](../../docs/decisions/0080-per-strategy-workspace-build-test-run-why.md),
[ADR 0082](../../docs/decisions/0082-strategy-root-mutable-strategies-auto-snapshots.md)); its live
arm / resume dialogs send `i_understand_live: true` only after an explicit checkbox, and its live
preflight list is informational, not a readiness gate. Each bot's detail page (`/deployments/{id}`,
listed on Portfolio at `/deployments`) offers the same pause / resume / stop / flatten / breaker-reset
dialogs, with the same checkbox on live resume (a bot running an earlier edit offers **Update bot**: a
managed stop followed by a new start on the current rules, each confirmed; live keeps the checkbox), and the Trade page (`/trade`) sends a live
`place-order` only after its live dialog's checkbox. Agents keep using this CLI.
Protection, leases, and live capital follow
[ADR 0058](../../docs/decisions/0058-protection-lifecycle-accounting.md): pause
(`lifecycle_command=stop_new_entries`) still maintains verified attached-child protection on
every worker poll, including empty `due` and feed-down. Default stop is managed shutdown
(protective brackets and residual occupancy stay in account risk). `--flatten` /
`POST /api/v1/deployments/{id}/stop?flatten=true` marketably exits then cancels remainders.
Live sizing uses allocated capital or venue available quote, never ledger `cash`. Workers hold
a 45s fenced lease; writes are revision-checked. UTC day-open and high-water baselines persist
across pause. Discover lease/lifecycle/latch fields on `thytrader-operator runtime` and capital
on `thytrader-runtime show`.

Account exposure and daily-loss fractions use one observed venue quote balance plus managed
long inventory cost and working buy-entry quote reservations across live risk-bearing books;
they never use one bot's allocation as the account denominator. Short proceeds and protective
orders do not add capital again. Bot allocations and portfolio caps still bind separately
([ADR 0106](../../docs/decisions/0106-account-risk-capital-and-live-startup-baselines.md)).
An unknown venue quote balance blocks entries even when an allocation exists. New live
strategy ledgers start with exact zero equity baselines; venue quote is not ledger cash.
`PORTFOLIO_EXPOSURE_EXCEEDED` / `PRODUCT_EXPOSURE_EXCEEDED` details name existing exposure,
proposed notional, account capital, and cap; `ALLOCATION_EXCEEDED` names the strategy reservation.
`BREAKER_MARK_MISSING` details distinguish missing inventory marks from missing equity baselines
and identify the affected deployment. Preserve the gate; diagnose through `decisions`, `show`,
`thytrader-operator reconciliation`, and `show-risk-policy` before changing policy.

In-app operator chat (`/chat`, `/api/v1/operator-chat`) may invoke these same HTTP routes. It is
not extra authority: mutations still need in-app confirmation, and live start, live resume, and
live place-order still need understand-live (chat sends `i_understand_live` only after that box).
Paper start and paper place-order tools may pass optional `maker_fee_rate` / `taker_fee_rate`
together ([ADR 0048](../../docs/decisions/0048-paper-deploy-fee-fields.md)); live rejects them.
`runtime_set_risk_policy` publishes the same `PUT /api/v1/risk-policy` document as this CLI,
including daily-loss / drawdown / rate / collar fields
([ADR 0050](../../docs/decisions/0050-daily-loss-drawdown-rate-collars.md))
and `allow_intra_strategy_pyramiding`
([ADR 0056](../../docs/decisions/0056-multi-instrument-documents-and-pyramiding.md)).
Coinbase secrets use `GET/PUT/DELETE /api/v1/credentials/coinbase`
([ADR 0053](../../docs/decisions/0053-workstation-ia-write-only-coinbase-credentials.md)); LLM
keys stay on `/chat` ([ADR 0051](../../docs/decisions/0051-in-app-operator-chat.md)).
Do not treat chat as this skill.

Live trading spends real money. Do not start live unless the user explicitly asked to arm live trading.

Credentials and market data. `set-coinbase-credentials` / `clear-coinbase-credentials` (or
`/settings`) write the shared credentials volume. The portfolio and execution workers reload them
within about 5 seconds **without restart**; the execution worker swaps its live broker, Coinbase
candles, quote reader, and user-order feed between cycles (audit `execution_venue_reloaded`).
Clearing credentials makes live books pause with `Live broker is unavailable.` Without
credentials, paper evaluates **synthetic demo candles**, not Coinbase prices: operator `runtime`
then reports component `execution_market_data` / `DEMO_MARKET_DATA`. The market-data ingest worker
keeps its startup provider until restarted (`make run` only when the user asked).

Paper may start on closed **venue-clock** bars of a strategy snapshot (`1m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, or `1d`). Live may start on the same clocks. Sub-hour live pauses (`mismatch_detail` `User-order feed is not connected.`) unless the user-order feed is connected and fresh; when that feed pause is the **only** reason (no operator pause, no other mismatch, no breaker latch) the worker resumes the book automatically once the feed is healthy (audit `user_feed_pause_cleared`). Operator pauses, other mismatches, and latches never auto-clear. A strategy's `htf_filter` and optional per-indicator extra timeframes evaluate last-completed complete-only bars; missing extra-TF or HTF coverage pauses. Paper and live do not bind frozen extra-TF or HTF dataset fingerprints. Ingest those extra clocks with `skills/thytrader-data/SKILL.md` before start. `place-order --timeframe` is the discretionary book clock (default `5m`; any ingested venue clock).

Required-clock warmup covers both the first decision's current and previous mapped bars.
At a 1h/4h rollover, a declared 50-bar HTF warmup can require 51 fetched candles; the worker
derives that coverage from the shared research mapping and keeps its deployment anchor across
restart. A complete archived dataset alone does not establish a healthy execution window.
For `HTF candle coverage is incomplete or not contiguous.`, inspect `decisions`, `show`,
operator `market-data`, and `reconciliation`; preserve the filter and risk limits. After repairing
coverage or updating a faulty worker, use explicit `resume --confirm --i-understand-live` for a
live bot and verify a fresh decision. Restart alone does not clear its persisted mismatch.

## Hard stop

When operating a running instance, do not edit `src/`, `compose.yaml`, Dockerfiles, Alembic, or tests.
Do not search the tree for a code patch. Report failures through this skill. Every command preflights
the full `/health/ready` ops contract. Rebuild or restart only with `make run` when the user asked,
or when the CLI reports a version or ops-contract mismatch, or HTTP 404 on an agent route while
`/health/ready` is 200 (the shared stale-image signal). Matching `0.1.0` alone is not current-image
evidence. Open the `ops/` workspace instead of the git root. Run every
`uv run thytrader-*` command from the repository root (the parent of `ops/`).

## Research load and runtime control

Backtests, studies, and portfolio backtests run in the separate `research-worker` service
([ADR 0092](../../docs/decisions/0092-research-worker-pool.md)), never in the API, so research
load does not slow runtime commands, the decision timeline, or the execution worker. A research
job reported as `queued` is waiting for a free research worker; it never blocks a deployment
start, pause, resume, or stop. To see how much research is waiting, read
`payload.research_workers` from `uv run thytrader-operator health`: `queue.queued`,
`queue.running`, `queue.oldest_queued_age_seconds`, and `live_workers` of `configured_workers`.
This lane never starts, cancels, or reprioritizes research; that is `thytrader-research`.

## Decision timeline (read-only)

`thytrader-runtime decisions UUID` answers "what did this bot decide on each bar, and why?" without
logs ([ADR 0087](../../docs/decisions/0087-per-bar-decision-timeline.md)). It reads
`GET /api/v1/deployments/{deployment_id}/decisions` (or, with `--strategy-id`,
`GET /api/v1/strategies/{strategy_id}/decisions`, optionally narrowed by `deployment_id`), newest
bar first. Each row is one completed bar of one covered product: `outcome` (`entry_signal`,
`no_signal`, `holding`, `exit`, `entry_blocked`, `skipped`, `error`), a one-line `summary` such as
`No trade: RSI(14) 47.21 needs ≥ 50`, the evaluated `rule` tree with leaf values versus thresholds
(a lagged indicator reads `Highest(3, high) (1 bar ago)` with the lagged value). Leaf operand
`value` / `previous_value` are rounded to 12 significant digits for reading; the exact values the
runtime compared are in `rule.signal.indicator_values` ([ADR 0094](../../docs/decisions/0094-research-honesty-and-agent-ergonomics.md)).
A false crossover says where the lines are: `EMA(9) is above EMA(21); no new cross this bar (…)`
when fast already sits above slow, `EMA(9) is below EMA(21); no cross above yet (…)` when it has
not reached it. Then come the `risk` verdict, `action` with `intent_id`/`orders`/`fills`, `skip_reason`/`exit_reason`, the
close price, and the end-of-bar position. `no_trade_bar: true` marks a flat zero-volume bar for an
interval without trades (its summary ends "(no-trade bar: no trades, flat at the prior close)"):
paper and live fill a bar Coinbase omitted between two traded bars exactly as research datasets do,
then evaluate it like any bar. A missing **newest** decision candle is never filled. When only
that candle is missing and history/cursor are contiguous, the worker waits until its UTC close
plus 120 seconds, with `skip_reason: bar_settling` and no new entries. Reconciliation and protection
maintenance continue; covered products wait together. At the deadline, or for older gaps, it
pauses with `data_gap`. Required HTF/indicator clocks and feed gates keep their existing policy
([ADR 0104](../../docs/decisions/0104-bounded-newest-candle-wait.md)). A matched
signal whose stop/target geometry or
sizing rested no order is `outcome: skipped` with `skip_reason` `entry_geometry` or `entry_sizing`
and a precise `reason_code` such as `TARGET_NOT_POSITIVE` (a short's target would be at or below
zero), `STOP_NOT_POSITIVE`, `NOTIONAL_BELOW_MINIMUM`, `QUANTITY_BELOW_VENUE_MINIMUM`, or
`INSUFFICIENT_CASH` ([ADR 0090](../../docs/decisions/0090-research-correctness-optional-take-profit-diagnostics.md)).
A position without a take-profit shows `target_price: null`. With `exits.signal_exit`
([ADR 0093](../../docs/decisions/0093-signal-based-exits.md)) every post-fill bar of an open book
carries `exit_rule` (`outcome` plus the evaluated tree with leaf values); a holding bar's summary ends
`· exit rule: <first unmet leaf>`. The bar the rule matched is `outcome: exit`, `exit_reason:
signal`, `reason_code: EXIT_SIGNAL` (`Exit (signal): <leaf> → sell …`), even while a live cancel of
the protection is still pending; the sell's fill may land on the next bar's row. Repeat `--outcome` to filter (trades are
`--outcome entry_signal --outcome exit`; blocked entries are `--outcome entry_blocked`). Pass the
response's `next_cursor` as `--cursor` for older bars. `storage: "unavailable"` means the API runs
without a database. The journal keeps the newest 20,000 decisions per bot (at most 180 days); it never
changes trading, and the same rows appear on the bot detail page and the strategy Why stage. The
operator lane exposes the same records as `thytrader-operator decisions`.

## Starting by strategy id

`start --strategy-id UUID` (HTTP `POST /api/v1/deployments` with `strategy_id`) starts from the
strategy's **current** saved definition: the server snapshots it and the bot records
`strategy_id` plus the snapshot `strategy_fingerprint` (and `strategy_name`). There is no publish
step and no fingerprint argument ([ADR 0082](../../docs/decisions/0082-strategy-root-mutable-strategies-auto-snapshots.md)).
Read the id from `thytrader-research list-strategies`. HTTP 404 `strategy_not_found`; HTTP 422
`strategy_invalid` (with `issues`) means the saved definition does not validate — fix it through
`skills/thytrader-research/SKILL.md`, never here. Editing a strategy never changes a running bot:
a bot whose `strategy_fingerprint` differs from the strategy's `current_fingerprint` is running an
**earlier edit**. To move it to the current rules, `stop UUID --confirm` (managed shutdown), then
`start --strategy-id UUID … --confirm` again (live also `--i-understand-live`) — only when the user
asked. Filter `list` output by `strategy_id`; HTTP `GET /api/v1/deployments?strategy_id=` does the
same server-side.

Strategy deletion (`thytrader-research delete-strategy`) is refused with HTTP 409
`strategy_has_active_deployments` while any bot of that strategy is running or paused; stopping it
is this lane's job and needs the user's request. After deletion, stopped live books remain with
`strategy_id: null`, `strategy_deleted: true`, and `strategy_name` kept; paper books of the
strategy are removed with it.

## Signal exits (paper and live)

A strategy whose snapshot declares `exits.signal_exit` ([ADR 0093](../../docs/decisions/0093-signal-based-exits.md))
exits an open book when that rule matches on a closed bar after the fill bar, with the same
semantics as the backtest. Paper fills the exit at that bar's close with the taker fee. Live first
cancels the book's protection (the attached bracket, the OCO, or the stop-only `stop_limit`), then
sends a marketable cover (intent purpose `signal_exit`), on the same race-safe path as time
exits and flatten. When Coinbase accepts the cancel but has not finished it, the bot waits
(`phase: pending_exit`, `position_state: exiting`, no pause) and the next cycle sells; it never re-rests protection in
between. The position shows `signal_exit_bar` (the bar whose rule matched) until it is flat, which
survives restarts. The protective stop still wins a same-bar tie, and pause or managed shutdown
keep these risk-reducing exits running. A failed exit-rule evaluation (for example missing
indicator-timeframe coverage) pauses a running bot with the evaluator's reason; the stop keeps
guarding the book. Portfolio sleeves need nothing extra: once flat, a sleeve's sizing cash is its
full allocation again.

## Reference instruments (paper and live)

A strategy may read up to three read-only reference series
(`data_requirements.reference_instruments`, indicator `source`;
[ADR 0096](../../docs/decisions/0096-reference-instruments.md)), for example a BTC-USDC 1d regime
gate on an alt. References are never traded: orders only go to the traded instrument.

- **Start gate.** `start` (and each portfolio sleeve) is refused with HTTP 409 unless every
  reference series is on the **enabled** market-data watchlist of the ingestion provider. The
  message names each missing series and the exact command, for example
  `uv run thytrader-data watch-add --product-id BTC-USDC --timeframe 1d --lookback-hours 2424
  --confirm` (the lookback covers the derived reference warmup). This lane never adds the watch:
  hand the command to the data lane (`skills/thytrader-data/SKILL.md`, needs the user's
  confirmation), then retry the start. In a portfolio start, only that sleeve reports `failed`
  with the same message; other sleeves start.
- **Every bar.** The bot loads each reference's closed bars every cycle. Only the last reference
  bar that had closed by the decision close is used (never an in-progress bar). Before a bar may
  open risk, each reference must have that bar plus a contiguous warmup window; otherwise the
  entry is **skipped, fail closed**, with decision row `outcome: skipped` and `skip_reason`
  `reference_data_stale` (the needed reference bar has not arrived) or `reference_data_missing`
  (no bars, a gap, or too little history), `reason_code` `REFERENCE_DATA_STALE` /
  `REFERENCE_DATA_MISSING`, and a `summary` naming the series and the bar it needed. The bot keeps
  running: stops, targets, trails, time and signal exits keep working, and the next fresh bar
  trades normally. A persistent stale reason means the reference watch is lagging: check it with
  `thytrader-operator data-catalog`, never by scraping logs.
- **Decision rows.** Evaluated rules show reference operands with the reference's base currency
  and clock, for example `BTC · EMA(100) [1d]`, with the exact values read.
- Multi-instrument documents (ADR 0056) apply one reference gate to every covered product on the
  shared bar. Portfolio sleeves need nothing else.

## Position state: open and protected vs exiting

Read `position_state` and `exit_in_flight`, not the raw `phase`, to tell what a book is doing
([ADR 0097](../../docs/decisions/0097-runtime-parity-and-observability.md)). The worker sets
`phase: pending_exit` as soon as any exit order works, including the TP/SL bracket (or the
stop-only `stop_limit`, or the paper take-profit) resting right after entry, so `pending_exit`
alone does **not** mean the bot is selling. `list` / `show` return `position_state` and
`exit_in_flight` on the deployment and on every `positions[]` row; `portfolio-status` sleeve bots
carry them too:

| `position_state` | Meaning |
| --- | --- |
| `flat` | No inventory. |
| `entering` | An entry rests; no inventory yet. |
| `open_protected` | Open, protection resting (TP/SL bracket, stop-only protection, or the paper synthetic stop plus any resting TP). |
| `open_unprotected` | Live inventory with no verified resting protection (see `protection_status`). |
| `open_unverified` | Live protection is unreconciled (`protection_status: unknown`). |
| `exiting` | The exit is in flight: a working marketable exit, a matched signal exit (`signal_exit_bar`), or a flatten. |

`exit_in_flight` is true only for `exiting`. A deployment takes its worst book (exiting, then
unprotected, then unverified, then protected). Open paper books that are not exiting are always
`open_protected`: the worker enforces the stop on every closed bar. Their `protection_status` is
`covered` on every read, `list` included, so the two fields agree
([ADR 0098](../../docs/decisions/0098-library-views-book-marks-portfolio-fills.md)).

`show` (`GET /api/v1/deployments/{id}`) also marks each `positions[]` row: `mark_price` is the
close of the newest bar the bot evaluated for that product (from the decision journal),
`marked_at` its UTC close, and `unrealized_pnl` the gross PnL at that mark (signed quantity times
the move from `entry_price`, before exit fees). All three are null without a journaled close;
`list` does not mark books.
`show` also returns `entry_fees` (paid entry fees allocated to held quantity) and
`unrealized_pnl_net` (gross PnL minus those fees). Prefer net when available. Partial exits
allocate entry fees proportionally; adds accumulate paid fees. Future exit fees are excluded,
so this is not a liquidation estimate. Missing, mismatched, or over-1000 applied current-window
fills leave both fee fields null, retaining the gross mark; never assume null means zero.
[ADR 0100](../../docs/decisions/0100-fee-adjusted-open-book-pnl.md).
On `show` (both summary and full HTTP detail), `ledger.mark_complete`, `marked_exposure`,
`total_net_pnl`, and `total_return_fraction` use the same per-product journaled closes as the
positions. Every open book needs its own mark; a missing close or unavailable journal leaves
aggregate PnL and exposure null. Reads never fetch a venue price to fill the gap. Use full
detail or the paged ledgers when historical fills or round-trip counts are needed.

## Same-bar exits (paper equals the backtest)

When one closed bar makes several exits due, paper takes the same one the backtest does
(ops contract `same_bar_exit_precedence`): the protective stop (at its pre-trail level), then a
take-profit the bar touched, then the signal exit, then the time exit. On the fill bar only the
stop is eligible. A bar that trades through the stop exits as `stop` even when `max_bars_held` is
also reached on that bar. Live brackets rest on Coinbase and resolve there.

## Portfolios

A portfolio ([ADR 0088](../../docs/decisions/0088-portfolio-model-and-portfolio-backtest.md),
[ADR 0091](../../docs/decisions/0091-portfolio-deployment-limits-and-manager-proposals.md)) is
composed with `skills/thytrader-portfolio/SKILL.md`; this lane deploys it. `portfolio-start
--portfolio-id ID --revision N` (the revision you reviewed with `thytrader-portfolio show`; stale is
HTTP 409 `portfolio_revision_conflict`) starts **one bot per sleeve**, tagged with the portfolio's
id: a paper sleeve starts with `weight × capital_quote` of paper cash, a live sleeve's
`allocated_capital` is `weight × capital_quote`. Sleeves already running or paused for this
portfolio are `attached`, not duplicated. Every sleeve is planned before anything starts, so one
refusal starts nothing: HTTP 422 `portfolio_start_rejected` lists `problems[]` (`sleeve_issue`,
`strategy_busy` when the strategy already runs standalone or in another portfolio — stop that bot
first, `strategy_invalid`, `timeframe_not_executable`, `risk_policy_refused` with the policy's
reason code). A live portfolio needs `--i-understand-live` on start and resume, configured
credentials (`live_credentials_missing`), and a published risk policy; **a live portfolio's sleeve
allocations count as risk-policy allocation membership for its own bots**, so an allocations
allowlist does not need to list sleeve strategies (standalone bots keep the allowlist; every other
policy rule — allowlist, slots, paper book, account exposure, breakers — still applies).

`portfolio-pause`, `portfolio-resume`, and `portfolio-stop [--flatten]` act on every sleeve, or on
one with `--sleeve-id` (sleeve id or strategy id), with the same semantics as single bots (pause =
no new entries, exits and protection continue; managed stop by default, flatten exits at market).
`portfolio-start --sleeve-id` starts one sleeve (for example a newly added one). Nothing to act on
is HTTP 409 `portfolio_not_deployed` / `portfolio_sleeve_not_deployed`. `portfolio-status` (read-only)
returns `state` (`not_deployed`, `running`, `partially_running`, `paused`, `stopped`), one
`sleeves[]` row per sleeve whose `deployment` field is that sleeve's bot (`deployment_id`, `status`,
`phase`, `position_state`, `exit_in_flight`, `lifecycle_command`, `net_pnl`, `exposure_quote`,
`open_books`, …) or `null` before the
sleeve starts, the breaker, and exposure against the caps.

Portfolio limits bind every sleeve: new entries must fit `max_total_exposure_fraction` and
`max_per_asset_fraction` of the portfolio's capital across its bots (decision reason codes
`PORTFOLIO_TOTAL_EXPOSURE_LIMIT`, `PORTFOLIO_ASSET_EXPOSURE_LIMIT`; `PORTFOLIO_LIMITS_UNAVAILABLE` fails
closed when the worker cannot read them). The optional `daily_loss_quote` and `max_drawdown_fraction`
stops are evaluated every worker cycle on the run's equity (capital plus each sleeve bot's net PnL);
a trip pauses every sleeve with `mismatch_detail` `PORTFOLIO_DAILY_LOSS_STOP:` /
`PORTFOLIO_DRAWDOWN_STOP:`, journals `breaker_tripped`, and **latches**: entries answer
`PORTFOLIO_BREAKER_LATCHED`, start and resume are refused (`portfolio_breaker_latched`), and a sleeve
resumed from its bot page is paused again. Only `portfolio-reset-breaker --confirm` (YOLO never)
clears it, re-baselining the day open and the peak at current equity; sleeves stay paused until
`portfolio-resume`. YOLO covers `portfolio-start/pause/resume/stop` by the portfolio's mode tier, like
single bots; `--i-understand-live` is never skipped.

## Commands

| Need | Command |
|---|---|
| Portfolio deployment state (read-only; each `sleeves[]` row's `deployment` is its bot or `null`) | `uv run thytrader-runtime portfolio-status --portfolio-id ID` |
| Start a paper portfolio (one bot per sleeve) | `uv run thytrader-runtime portfolio-start --portfolio-id ID --revision N [--maker-fee-rate 0.001 --taker-fee-rate 0.002] --confirm` |
| Start a live portfolio | `uv run thytrader-runtime portfolio-start --portfolio-id ID --revision N --confirm --i-understand-live` |
| Start one sleeve | `uv run thytrader-runtime portfolio-start --portfolio-id ID --revision N --sleeve-id ID --confirm [--i-understand-live]` |
| Pause a portfolio (or one sleeve) | `uv run thytrader-runtime portfolio-pause --portfolio-id ID [--sleeve-id ID] --confirm` |
| Resume a portfolio (or one sleeve) | `uv run thytrader-runtime portfolio-resume --portfolio-id ID [--sleeve-id ID] --confirm [--i-understand-live]` |
| Stop a portfolio (managed, or flatten) | `uv run thytrader-runtime portfolio-stop --portfolio-id ID [--sleeve-id ID] [--flatten] --confirm` |
| Reset a latched portfolio breaker | `uv run thytrader-runtime portfolio-reset-breaker --portfolio-id ID --confirm` |
| List deployments | `uv run thytrader-runtime list` |
| Show one snapshot | `uv run thytrader-runtime show UUID` |
| Per-bar decisions of one bot (read-only) | `uv run thytrader-runtime decisions UUID [--outcome no_signal] [--limit 50] [--cursor C]` |
| Decisions across a strategy's bots | `uv run thytrader-runtime decisions --strategy-id UUID [DEPLOYMENT_UUID] [--outcome entry_signal --outcome exit]` |
| Start paper | `uv run thytrader-runtime start --strategy-id UUID --mode paper --cash 10000 --confirm` |
| Start paper with fee assumptions | `uv run thytrader-runtime start --strategy-id UUID --mode paper --cash 10000 --maker-fee-rate 0.001 --taker-fee-rate 0.002 --confirm` |
| Start live | `uv run thytrader-runtime start --strategy-id UUID --mode live --confirm --i-understand-live` |
| Pause | `uv run thytrader-runtime pause UUID --confirm` |
| Resume paper | `uv run thytrader-runtime resume UUID --confirm` |
| Resume live (re-arms orders) | `uv run thytrader-runtime resume UUID --confirm --i-understand-live` |
| Stop (managed shutdown) | `uv run thytrader-runtime stop UUID --confirm` |
| Stop and flatten | `uv run thytrader-runtime stop UUID --flatten --confirm` ([ADR 0058](../../docs/decisions/0058-protection-lifecycle-accounting.md)) |
| Clear latched breakers | `uv run thytrader-runtime reset-breaker-latches UUID --confirm` ([ADR 0064](../../docs/decisions/0064-deployment-http-lifecycle-and-breaker-latch-reset.md)) |
| Place paper long | `uv run thytrader-runtime place-order --mode paper --product-id BTC-USDC --timeframe 5m --side long --origin agent --entry-kind post_only_limit --limit-price 100000 --quantity 0.01 --stop-price 90000 --take-profit-price 120000 --idempotency-key KEY --cash 10000 --confirm` |
| Place paper short | `uv run thytrader-runtime place-order --mode paper --product-id BTC-USDC --timeframe 5m --side short --entry-kind post_only_limit --limit-price 100000 --quantity 0.01 --stop-price 110000 --take-profit-price 90000 --idempotency-key KEY --cash 10000 --confirm` |
| Place live long | `uv run thytrader-runtime place-order --mode live --product-id BTC-USDC --timeframe 1h --entry-kind marketable --quantity 0.01 --stop-price 90000 --take-profit-price 120000 --idempotency-key KEY --confirm --i-understand-live` |
| Place live short | `uv run thytrader-runtime place-order --mode live --product-id BTC-USDC --timeframe 1h --side short --entry-kind marketable --quantity 0.01 --stop-price 110000 --take-profit-price 90000 --idempotency-key KEY --confirm --i-understand-live` |
| Show risk policy | `uv run thytrader-runtime show-risk-policy` |
| Publish risk policy | `uv run thytrader-runtime set-risk-policy --quote-currency USDC --product-allowlist BTC-USDC --max-concurrent-running-deployments 8 --max-concurrent-open-positions 8 --max-portfolio-exposure-fraction 1 --per-product-max-exposure-fraction 1 --paper-capital-quote 100000 --confirm` |
| Publish risk policy with pyramiding | `uv run thytrader-runtime set-risk-policy --max-concurrent-running-deployments 8 --max-concurrent-open-positions 8 --max-portfolio-exposure-fraction 1 --per-product-max-exposure-fraction 1 --paper-capital-quote 100000 --allow-intra-strategy-pyramiding --confirm` |
| Publish risk policy with absolute caps and a venue budget | `uv run thytrader-runtime set-risk-policy --max-concurrent-running-deployments 8 --max-concurrent-open-positions 8 --max-portfolio-exposure-fraction 1 --per-product-max-exposure-fraction 1 --paper-capital-quote 100000 --max-daily-loss-quote 2500 --max-portfolio-exposure-quote 50000 --max-venue-order-actions-per-minute 90 --confirm` |
| Show YAML settings | `uv run thytrader-runtime show-settings` |
| Set YOLO paper without restart | `uv run thytrader-runtime set-settings --yolo-enabled true --yolo-tiers paper --confirm` |
| Show Coinbase credential flags | `uv run thytrader-runtime show-coinbase-credentials` |
| Set Coinbase credentials | `uv run thytrader-runtime set-coinbase-credentials --api-key-name organizations/…/apiKeys/… --private-key-file ./coinbase.pem --confirm` |
| Clear Coinbase credentials | `uv run thytrader-runtime clear-coinbase-credentials --confirm` |

`list`, `show`, `decisions`, `portfolio-status`, `show-risk-policy`, `show-settings`, and `show-coinbase-credentials` are read-only and do not use `--confirm`. Optional
`--product-allowlist BASE-QUOTE` (for example `BTC-USDC`, matching `--quote-currency`; USDC is the
default quote) and `--allocation STRATEGY_UUID:QUOTE` may be repeated. `STRATEGY_UUID` is the
strategy's `strategy_id` (not the `sha256:` fingerprint): read it from
`thytrader-research list-strategies` or `strategy_id` on `thytrader-runtime show UUID`.
**Allocations gate LIVE only:** once any allocation is published, live becomes an allowlist —
live `place-order` (discretionary books) is denied and every strategy without its own allocation is
denied at live start and live entry. **Paper research is not blocked:** unlisted strategies and
paper discretionary tickets still start and trade, sized by `--paper-capital-quote`; a listed
strategy's paper starting cash stays bounded by its allocation (a rehearsal of the live
reservation). The allocations may not sum above `--paper-capital-quote`. Omit `--allocation`
unless the user wants exactly that live restriction.

`list` and `show` return `positions[]`, `instrument_runtimes[]`, product-tagged `orders`/`fills`,
`book_totals` (`open_books`, `working_orders`, `fill_count`) that must match those collections
([ADR 0060](../../docs/decisions/0060-multi-book-deployment-api.md)), the snapshot's
`timeframe` (copied from the strategy snapshot when the stored deployment row is null;
[ADR 0064](../../docs/decisions/0064-deployment-http-lifecycle-and-breaker-latch-reset.md)),
ADR 0058 lifecycle fields
(`lifecycle_command`, `daily_loss_latched`, `drawdown_latched`, `revision`, `worker_lease_held`;
[ADR 0064](../../docs/decisions/0064-deployment-http-lifecycle-and-breaker-latch-reset.md)), and a
`capital` block with `allocated_capital`, `venue_available_quote`, `reserved_buying_power`,
`inventory_cost`, `performance_equity`, `performance_capital_quote`,
`performance_maximum_drawdown_fraction`, `initial_equity`, `baseline_equity`,
`high_water_mark_equity`, and `utc_day_open_equity`
([ADR 0065](../../docs/decisions/0065-deployment-capital-accounting-http.md)). Top-level `cash` is
ledger fill accounting only. `reserved_buying_power` counts working entry remainders;
known protective or other exit intents never reserve entry quote, including paper limit exits.
Missing intent evidence stays conservatively counted. Live sizing uses `capital.allocated_capital` or
`capital.venue_available_quote` (null when unknown). The singular `position` field is
compatibility-only (focused book, always includes `product_id` and `compatibility_focus`). Read
`positions` for inventory. `--i-understand-live` is unchanged. `set-risk-policy` optional breaker
flags default to the compiled envelope: `--daily-loss-limit-fraction 1`,
`--max-strategy-drawdown-fraction 1`, `--max-entry-orders-per-minute 60`,
`--max-cancellations-per-minute 60`, `--reference-price-collar-fraction 0.5`. Daily-loss and
drawdown trips pause risk-increasing orders (exits continue). Rate and collar denies do not pause.
Pass `--allow-intra-strategy-pyramiding` when paper/live same-side adds should be legal; the
strategy must also enable `entry.pyramiding`. Omitted (false) keeps compiled-default
policy bytes stable. Schema-enabled pyramiding without this flag is denied
(`PYRAMIDING_NOT_ALLOWED`). Backtests follow the strategy document only. `set-risk-policy`
requires `--confirm` and does **not** require `--i-understand-live`. `reset-breaker-latches`
always requires `--confirm`; YOLO never skips it.

Ops contract v64 / Alembic `0061` advertises `capital_normalized_performance`
([ADR 0107](../../docs/decisions/0107-capital-normalized-live-performance.md)).
`show UUID` exposes `capital.performance_capital_quote`, the pinned budget for percentage
returns/drawdown, and `capital.performance_maximum_drawdown_fraction`, the worst verified
fraction observed. Live ledger equity remains PnL from zero. Allocated starts pin their budget;
unallocated starts pin the first known positive venue sizing balance before entry. Legacy books
pin their positive opening balance or first verified budget after upgrade. Rebalance and venue
balance changes do not reset it. Compare `thytrader-operator performance --deployment-id UUID`
against this budget, not today's allocation. Missing positive capital or marks deny new entries
(`BREAKER_MARK_MISSING`). The drawdown breaker uses current loss from its durable peak; the
reported maximum retains fill-event and worker observations across recovery/restart. Latch
reset does not erase the peak/history or resume the bot; a continuing breach can trip again.

**Live start and live on-demand orders require a published policy** ([ADR 0063](../../docs/decisions/0063-stage-5-release-discipline-ci-risk-defaults-rate-budget.md)):
the compiled default is a wide **paper** research envelope, not a live-safe default. A fresh
install's `start --mode live` or `place-order --mode live` is denied
(`LIVE_REQUIRES_PUBLISHED_POLICY`) until `set-risk-policy --confirm` has published at least one
version. Paper is unaffected. Optional `--max-daily-loss-quote` and
`--max-portfolio-exposure-quote` add an absolute quote ceiling alongside the matching fraction
(whichever binds tighter trips first). These absolute ceilings protect real money and apply to
**live only**; paper keeps the capital fractions of `--paper-capital-quote`. Unset by default, since this skill does not assert a
universal safe amount for every operator. Optional `--max-venue-order-actions-per-minute` adds a
combined cap across entry-order and cancellation requests in the same rolling minute — a coarse
venue-request budget that can only ever deny a **new entry**, never a cancellation or protective
(stop/take-profit/time-exit/bracket) submission, since only new-entry admission calls this check.
`--max-entry-orders-per-minute` itself now counts only entry-purpose orders, so recent protective
activity no longer exhausts it and blocks an unrelated new entry.
`place-order` is confirmation-gated. Live place-order also requires `--i-understand-live`.

Strategies may use per-operand `offset` (0–500) in entry, signal-exit, and HTF-filter rules
([ADR 0099](../../docs/decisions/0099-operand-level-indicator-offsets.md)). Require ops contract
`thytrader-ops-contract-v64` with `indicator_operand_offset_runtimes` including the deployment
mode. These reads lag completed bars on the indicator's own clock, add to declaration offsets,
and require extra warmup. Missing history remains undefined. Decision journals expose lagged
values as `id@N` / `id.series@N` and labels show the combined lag. Authoring stays in the research
lane; start/resume and live acknowledgement gates stay the same.
Optional `--note` is frozen onto the why-trade record at persist. Later review notes use
`thytrader-memory add-trade-reason-note --confirm` (YOLO never covers that lane).
`--side` defaults to `long`; pass `short` for a spot sell-to-open. Live shorts fail closed without
available base and never borrow. When SL/TP are known and trailing is off, live uses an
attached bracket on the entry; paper still uses synthetic exits. A strategy with
`take_profit: {"kind": "none"}` never attaches a bracket: paper rests no take-profit (its stop is
synthetic on closed bars), and live rests one Coinbase **stop-limit** after the fill
(`kind: stop_limit`, intent purpose `bracket`) triggered at the working stop with its limit 5%
through it — the same offset as a bracket's stop leg. Trailing ratchets replace it; time exits,
signal exits, and flatten cancel it first. A stop-limit can rest unfilled if price gaps through its limit, exactly
like a bracket stop leg ([ADR 0090](../../docs/decisions/0090-research-correctness-optional-take-profit-diagnostics.md)). `--timeframe` defaults to `5m`; pass `1m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, or `1d` for
another book clock. Paper `start` and paper `place-order` accept optional `--maker-fee-rate` and
`--taker-fee-rate` together (Decimal strings in `[0, 0.1]`, maker ≤ taker). Omitted paper rates
use the documented `0.001` / `0.002` assumptions. They are **not** observed Coinbase fees. To model
what the account actually pays, pass `suggested_maker_fee_rate` / `suggested_taker_fee_rate` from
`thytrader-operator fees` (the account's reported Coinbase rates, `suggestion_source:
coinbase_account`); the `schedule_*` rates there are context only. Live
rejects those flags; live fills stay venue-recorded through cursor-terminated List Fills
([ADR 0059](../../docs/decisions/0059-coinbase-list-fills-cursor-pagination.md)). Incomplete or
unparseable Coinbase fill pages fail closed (`BrokerError`); do not treat them as a complete
empty remainder. YOLO may skip `--confirm` for paper start/pause/resume/stop/place-order
when the `paper` tier is enabled, and for live start/pause/resume/stop when the `live` tier
is enabled. Live place-order, `set-risk-policy`, `set-settings`, and Coinbase credential set/clear
never skip `--confirm`. YOLO never covers credentials. Pass `--private-key-file`; never a CLI
secret or pasted PEM. Setting credentials does not arm live trading. Repeat the same
`--idempotency-key` instead of retrying a timeout.

## Rejected and unconfirmed live orders

The worker never re-submits an order automatically
([ADR 0078](../../docs/decisions/0078-live-readiness-http-ack-venue-reload-definite-rejects.md)).

- **Definite rejection.** Coinbase `success=false`, or HTTP 400/401/403/404/422 on create, means no
  order exists. The order is `rejected` with `reject_reason` such as
  `coinbase_http_400:INVALID_ARGUMENT` (audit `order_submit_rejected`). The book returns to flat
  and keeps running; fix the cause (size, permissions, product) before the next entry. A rejected
  protective bracket still pauses the book because the position is unprotected.
- **Repeated protective rejections are latched.** A live bracket, stop-limit, or marketable exit that Coinbase
  rejects (for example `INSUFFICIENT_FUND`) pauses once with `mismatch_detail` starting
  `PROTECTIVE_SUBMIT_REJECTED:` (audit `protective_submit_latched`, one per real rejection).
  Identical re-submits back off 1, 2, 4 … minutes (capped at 30); after 5 identical rejections the
  worker waits for `resume` before one more attempt. A changed stop, target, or quantity starts
  fresh. Check Coinbase balances and holds before resuming; do not place manual orders.
- **Exit ordering on live.** Before any exit or protection the worker learns the entry's
  venue-attached TP/SL child. Time, stop, and flatten exits cancel known protective children first.
  Coinbase cancels asynchronously: an accepted cancel shows the order still `open` with
  `reject_reason` `cancel_pending`; the worker re-checks it with GET order each poll (no re-cancel)
  and sends the exit once it is gone. That wait is not a pause. A `FILLED` exit whose REST fills
  lag keeps the book `pending_exit` until the fills are ingested; nothing else is sent meanwhile.
- **Stop with flatten.** While a flatten is pending no new protective bracket is rested. The book
  stays `stopped` throughout (a fault records `mismatch_detail` but never flips it to `paused`) and
  ends `stopped` / `flat` with no `mismatch_detail` (audit `flatten_settled` when it had to
  correct a paused or faulted book). A paused book whose position-only fault (for example
  `Live bracket could not be rested on an open position.`) no longer applies because it is flat
  shows `Position is flat and no orders are working; …` instead.
- **Ambiguous outcome.** Timeouts, HTTP 408/409/429/5xx, and transport failures leave the order
  `unknown` with no venue id (audit `order_submit_unconfirmed`, operator finding
  `UNKNOWN_ORDERS`). Every poll the worker looks it up at Coinbase by `client_order_id` (same
  product, orders created from five minutes before submit). If Coinbase shows it, the worker
  adopts the venue id and status (audit `unconfirmed_order_recovered`); then `resume`.
  If a complete scan finds nothing, the book stays paused with `mismatch_detail`
  `Order submit is unconfirmed: no Coinbase order with client_order_id … was found; …`
  (audit `unconfirmed_order_not_found`). Resuming does not clear it; the next poll re-pauses.
- **Operator recovery for a not-found unconfirmed order.** Do not retry the order. Ask the user to
  check Coinbase Advanced Trade orders (the product, around the submit time) for that
  `client_order_id` and for any matching fill. If Coinbase shows the order, wait: the worker
  adopts it on the next poll. If the user confirms no such order or fill exists, stop the
  deployment (`stop UUID --confirm`, managed shutdown) and start a new one; the stopped book keeps
  the unknown order in its history and any priced remainder may still count in account risk until a
  contributor resolves it.

Underlying HTTP:

- `GET /api/v1/deployments?limit=&offset=` (summary rows; no historical orders/fills)
- `GET /api/v1/deployments/{id}?detail=summary|full` (default `summary`)
- `GET /api/v1/deployments/{id}/fills?limit=&cursor=` and `/orders?limit=&cursor=`
- `POST /api/v1/deployments` (mode `live` requires `"i_understand_live": true`, else 428)
- `POST /api/v1/deployments/{id}/pause`
- `POST /api/v1/deployments/{id}/resume` (live books require body `{"i_understand_live": true}`, else 428)
- `POST /api/v1/deployments/{id}/stop` (optional `?flatten=true`; default is managed shutdown)
- `POST /api/v1/deployments/{id}/reset-breaker-latches`
- `POST /api/v1/discretionary-orders` (mode `live` requires `"i_understand_live": true`, else 428)
- `GET/PUT /api/v1/risk-policy`
- `GET/PUT /api/v1/settings` (YAML non-secrets and YOLO; no secret echo; [ADR 0055](../../docs/decisions/0055-yaml-settings-runtime-reloadable-yolo.md))
- `GET/PUT/DELETE /api/v1/credentials/coinbase`

## Confirmation

- Never mutate unless the user explicitly asked **and** `--confirm` is present, unless the user
  explicitly asked to operate under YOLO **and** operator `configuration` /
  `thytrader-playbook status` shows the matching tier (`paper` or `live`) enabled.
- Never start live, resume a live deployment, or place a live order without
  `--i-understand-live`. YOLO never skips that flag. Live start/pause/resume/stop may omit `--confirm` only when the `live` tier is enabled
  and the skip audit succeeds. Live `place-order`, `set-risk-policy`, `set-settings`, and Coinbase
  credential set/clear never YOLO.
- Fail closed if YOLO is off, the needed tier is absent, or the skip audit is unavailable.
  Do not retry with extra flags unless the user asked you to.
- Successful mutations print JSON identities (`id`, `mode`, `status`, `kind`, optional
  `strategy_id`, `strategy_fingerprint`, `strategy_name`). Keep those identities.
- Watch status after a mutation with `uv run thytrader-operator runtime --deployment-id UUID`.

YOLO on/off and independent tiers live in `thytrader.yaml` (loopback `/settings`, or
`set-settings --confirm`). Leftover `THYTRADER_YOLO_TIERS=paper` is valid. YAML wins leftover env
and applies without restart. Secrets stay out of YAML. Live still needs `--i-understand-live`.

## Forbidden

- Using this skill because you can observe a runtime
- Folding these commands into operator or research skills
- Printing API keys, private keys, `.env` values, or database URLs
- Direct PostgreSQL access
- Cancelling individual Coinbase orders
- Publishing a risk policy without `--confirm`, or treating that mutation as live arming
- Setting or clearing Coinbase credentials without `--confirm`, printing the PEM, or treating
  a credentials write as live arming
- Treating a timeout as proof the start/pause/stop/place-order failed; `show` the deployment and reconcile before retrying
- Re-submitting or duplicating an order whose submit is unconfirmed; follow the recovery above
- Sending `i_understand_live` over HTTP without the user's explicit live acknowledgement
- Editing application source to arm, pause, or change execution on a running instance


## Explicit paper/live comparison twins

Ops contract v63 advertises `runtime_observability: explicit_deployment_twins` (Alembic `0060`,
[ADR 0102](../../docs/decisions/0102-explicit-paper-live-twin-links.md)). Run from the repository root:

```bash
uv run thytrader-runtime show-twin BOT_ID
uv run thytrader-runtime link-twin BOT_ID --counterpart-deployment-id OTHER_BOT_ID --confirm
uv run thytrader-runtime unlink-twin BOT_ID --counterpart-deployment-id OTHER_BOT_ID --confirm
```

Either member can be the target. Read is `GET /api/v1/deployments/{id}/twin`; linking is `PUT`
with only `counterpart_deployment_id`; unlinking is `DELETE` with that expected id as a query
parameter. Response: `{deployment_id, twin: null | {paper_deployment_id, live_deployment_id,
linked_at}}`. Mutations **always require `--confirm`; YOLO never covers twin links**. No
`--i-understand-live` is needed: this selects comparison metadata and cannot start, stop, resume,
arm, or submit orders. Leave deployment authority in its existing commands.

Choose one paper and one live **strategy** bot on the same primary product and timeframe.
They must share a snapshot or have server-verified identical pinned trading rules across clones
(ADR 0105): only root identity, name, description, creation time, and metadata may differ. Status, portfolio membership, capital, and fees may differ.
Discretionary books and different rules return 422; missing ids return 404. A bot has at most
one partner: another partner returns 409. Unlink the current pair before selecting a replacement.
The same link is idempotent and retains `linked_at`. Unlinking an already absent pair is
idempotent; a changed partner returns 409. After a timeout/error, read `show-twin` before retrying.

Comparisons in `thytrader-operator portfolios` and `thytrader-portfolio fill-comparisons` use
saved links only (newest-linked first, up to 10); unlinked books have no comparison. Existing
bots are not automatically linked by the migration. Linking survives worker saves and restarts.
The UI offers the same confirmed controls on Bot detail under **Paper/live twin**.

Ops contract v63 adds `runtime_observability: rule_matched_deployment_twins`
([ADR 0105](../../docs/decisions/0105-rule-equivalent-clone-twins.md)). Intended paper/live clones
can link when their pinned rules match exactly; the server ignores only root id, name, description,
creation time, and metadata. Each fill-comparison side exposes its actual `strategy_fingerprint`;
the top-level fingerprint remains the paper-side reference. This never changes a bot or its rules.

## Optional entry economics and protection trace (ADR 0109)

An explicitly authored `entry.economic_guard` applies the frozen minimum net maker
target return after both fees in paper/live as in research. It requires a TP; live
requires a current fee profile. Refusals appear as `NET_TARGET_BELOW_MINIMUM` or
`ECONOMICS_FEE_UNAVAILABLE`. Do not edit a deployed snapshot to enable the guard.
The same observed tier reaches replacement entry sizing; repricing cannot bypass
the optional guard. Strategies without it retain their existing behavior.
Use the research skill to author and validate a new snapshot, then the existing runtime
confirmation and live-acknowledgment gates to deploy only when requested. New decision
rows display protective replacement identities and confirmed working coverage without
rewriting historical evidence. Simulation `execution_stress` is research-only.

## Stopped books, pauses, and missing candles (ADR 0110)

An operator pause does not stop reconciliation. Every watched order and attached child is
still reconciled; a genuine new fault is kept and the book is not unpaused. A missing
decision-candle window journals `data_gap`, blocks new entries, and still reconciles live
orders and fills. Cold-cache history rebuilding is a transient wait, **not** evidence of a
venue data gap: it neither pauses nor resumes the bot or advances its decision cursor.
Reconciliation and native protection from persisted stop/target levels continue when a fresh
venue context exists, without evaluating incomplete signal/ATR history. No candle or price
is fabricated. Managed stop and explicit flatten stay distinct. Flatten without a verified
closed price keeps protective orders and reports pending (`Flatten is pending: no verified
closed price...`), not success; a genuine reconciliation fault takes precedence in the detail.
Only an enabled product's **most-recent closed, traded** provider candle (including a preview)
may price that exit; stale, in-progress, and synthesized no-trade bars cannot. Stopped
discretionary books and every covered book, including a sole secondary position, stay
supervised. Cancel/fill races, late fills, and unknown cancels remain supervised across
worker restarts. Diagnose with `show` and `thytrader-operator reconciliation`; do not
treat a pending flatten as flat.
