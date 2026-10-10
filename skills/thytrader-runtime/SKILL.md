---
name: thytrader-runtime
description: >-
  Start, pause, resume, or stop ThyTrader paper and live deployments and
  whole portfolios (portfolio-start/pause/resume/stop, one bot per sleeve, and
  portfolio-reset-breaker), read their per-bar decision timeline (read-only
  `decisions`), publish the risk-policy registry, run fleet controls
  (fleet-preview/status/disarm/stop/flatten/rearm), link paper/live twins, set
  YAML settings, show/set/clear write-only Coinbase credentials, and adopt or sell
  coins already held in the Coinbase account (adoption-preview, place-order
  --entry-kind adopt, sell-holdings, start --adopt-holdings), through the
  confirmation-gated thytrader-runtime CLI. Use when the user
  explicitly asks to deploy, pause, resume, stop, place an on-demand order, protect
  or sell held coins, set the risk policy, or manage Coinbase API secrets. Requires
  --confirm on every mutation unless YOLO covers that tier. Live start, live resume,
  live place-order, and sell-holdings also require --i-understand-live (sent as HTTP
  i_understand_live=true).
  YOLO live may skip --confirm on start/pause/resume/stop
  only. Credential set/clear always need --confirm; YOLO never covers them.
  Publishing a risk policy or setting credentials does not arm live trading.
  Never diagnose through this skill and never submit Coinbase orders directly.
---

# ThyTrader runtime

Confirmation-gated paper and live **control**, including the risk-policy registry and write-only
Coinbase Advanced Trade credentials. This skill is not an extension of `thytrader-operator` or
`thytrader-research`.

HTTP-only against the loopback API (base URL and installation Bearer auth: [shared
rules](../README.md#shared-rules-every-lane)). There is no `--local` database mode.
Browser mutations additionally require CSRF
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
a 45s fenced lease; writes are revision-checked. Scoped parent/runtime writes commit together;
a rejected revision has no partial runtime effect. Distinct same-book fills serialize local
financial projection, not external venue execution. Reused discretionary entry conflicts before
submission if concurrent state changes; read fresh state and re-admit, never force stale financial
fields under a new revision. Replacement protection reconciles cancel-time executions and waits
for complete applied economics before sizing; missing publication is not permission to infer a
sell quantity. Peer breaker pauses preserve current cash, lifecycle intent and other latches
([ADR 0121](../../docs/decisions/0121-execution-write-boundaries.md)).
UTC day-open and high-water baselines persist
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

Daily loss is not the exposure set ([ADR 0111](../../docs/decisions/0111-durable-risk-accounting-scopes.md)).
Exposure and order-rate occupancy still count running, paused, and stopped books that hold
inventory or working entries. UTC-day loss also includes stopped flat books of the same mode and
the same spot quote, including a fill dated today after stop. Stopping a book does not reset that
loss or either latch. A drawdown latch blocks only the matching strategy, or a discretionary book
on that product; it does not block unrelated strategies. USD, USDC, and USDT losses are never
added together. An open book without a same-UTC-day baseline denies new risk and does not invent
equity. `reset-breaker-latches` clears latch flags only; a loss still over the limit can trip
again, and the bot stays stopped or paused until a separate resume. Paper and live strategy
deletion retain stopped books, fills, latches, and referenced snapshots, detached from the deleted
strategy; deletion is not a reset. A null strategy FK does not turn that strategy's drawdown into
discretionary drawdown. Daily loss pauses only running books of that mode and quote; deliberate
pauses and stopped choices are preserved. A first discretionary denial can leave its latch on a
persisted same-quote peer even though no new candidate book was created. Reset the specific row
carrying the latch, not unrelated books. Risk checks reload fresh complete accounting for every
retained book; a product-focused view or cached portfolio cannot hide sibling fills.
Older books need verified opening evidence ([ADR 0120](../../docs/decisions/0120-verified-risk-opening-evidence.md)):
complete applied fills reconstruct midnight cash and separate product quantities. A genuinely
flat midnight needs no price; overnight inventory, including a later closure, needs an actual
closed midnight mark for each product. The worker can recover an exact complete hourly range.
Missing/inconsistent economics, filled orders missing fills, or unavailable opening prices deny
rather than becoming zero loss. An OPEN/PENDING_EXIT product runtime without its own positive
position is also unresolved, even when another product's position survives. It blocks new-entry
admission without a price observation and cannot certify midnight equity or flat-day zero PnL. Unapplied live fills deny until economics reconcile. Neither
maintenance nor a late restart promotes current equity or legacy stamps into midnight evidence.
Paper starts share one policy funding envelope, not a separate envelope per quote: occupied
foreign-quote paper books deny new funding rather than reuse or convert that budget. Retained
stopped flat loss evidence does not occupy funding. Concurrent multi-quote paper funding is not
supported by the current scalar paper-capital policy.

`--max-concurrent-running-deployments` and `--max-concurrent-open-positions` accept 1–128 per mode
(running and paused bots occupy a running slot), and `--product-allowlist` up to 256 products.
Exposure caps, not these counts, bound the capital at risk.

Optional `set-risk-policy` flags `--max-order-quantity`, `--max-order-notional-quote`, and
`--min-available-quote-reserve` are unset by default; compiled defaults and old stored policy
hashes stay unchanged. Publication replaces the policy, not patches it: resupply any configured
optional bounds you intend to keep. When set, they deny only an exceeding entry (`MAX_ORDER_QUANTITY`,
`MAX_ORDER_NOTIONAL`, `BALANCE_RESERVE`). Monetary bounds require matching `--quote-currency`;
there is no USD/USDC conversion. Reserve is **notional admission headroom, not a guaranteed
post-fill balance**: live fees/slippage are unknown and not guaranteed covered. Live subtracts
candidate notional and local unheld buy remainders from observed venue available quote, not
confirmed venue holds a second time; ambiguous holds deny. Paper includes occupied-book cash
changes (recorded fees/loss), working buy reserves, and conservative stored/default paper taker
fees. Unknown quote, quantity when capped, or paper opening cash fails closed. Limits never gate
protective exits. These are observation-time checks, not atomic reserves against external trades.

Optional fleet entry clustering cap ([ADR 0125](../../docs/decisions/0125-correlation-aware-risk-limits.md)):
`--max-fleet-entries-per-window N` (1–128) with `--fleet-entry-window-minutes W` (1–1440); set
both or neither. Unset by default, and an unset cap keeps old policy fingerprints. When set, a
new entry is denied with `FLEET_ENTRY_CLUSTER_LIMIT` once N distinct bot/product pairs in that
mode placed an entry intent in the last W minutes. Stopped and paused bots still count, adoption
intents do not, and paper and live are counted separately. Pyramid adds are gated and counted. A
reprice of an already working entry is not gated, but its replacement intent counts for other
bots. In-kind adoption (`--entry-kind adopt`, `start --adopt-holdings`) and protective exits are
never gated. The denial does not pause the bot: that bar's signal is skipped and recorded in
`decisions`. The detail names the count, window, cap, the oldest counted entry, and when a slot
frees. Which bots win a crowded bar depends on worker order, which is not a priority. API and
worker admissions racing can exceed the cap by one. Resupply both flags on every publication to
keep the cap. Starting point for a ~500 USDC fleet whose bars close together on 2h boundaries:
`--max-fleet-entries-per-window 4 --fleet-entry-window-minutes 120`. Backtests do not apply it.

Optional BTC-beta-weighted exposure cap ([ADR 0125](../../docs/decisions/0125-correlation-aware-risk-limits.md)):
`--max-btc-beta-exposure-fraction F` (in (0, 1], of the same capital base as the exposure caps)
and/or `--max-btc-beta-exposure-quote Q` (live only; the tighter wins). Both are unset by
default. While unset, nothing changes, no history is read, and old fingerprints are kept. When
set, the gate weights each product's exposure (position cost plus working entries, counted
gross, so shorts never hedge) by its β against `BTC-<quote>` (BTC-USDC for USDC bots, β 1 by
definition). β is the slope of 90 settled UTC daily log returns, rounded up to 0.01 and clamped
to [0, 3]. It sums over every same-quote running, paused and stopped bot that holds or works
risk, adds the new entry's notional × β, and denies `BTC_BETA_EXPOSURE_EXCEEDED` above the
cap. The detail names existing, proposed×β, cap, capital, fraction and absolute.
**Fail closed:** if the new product or **any held same-quote product** has fewer than 60 daily
returns, cannot be read, or has a last daily bar more than 48 h old, every new entry in that
quote is denied with `BTC_BETA_UNAVAILABLE`. The detail names the product and the cause
(`insufficient_history n=…<60`, `stale last_close=…`, `fetch_failed`, `not_loaded`). Remove a
newly listed coin from `--product-allowlist` (and close its position) or unset the cap; there
is no default β. Each process reads each product's daily bars once per UTC day (00:02 UTC), and
an outage keeps the previous day's estimate for up to 48 h. The cap applies to strategy,
lockstep, portfolio-sleeve and discretionary entries, pyramid adds, reprices (on the remaining
notional) and in-kind adoption (`--entry-kind adopt`, `start --adopt-holdings`). It never
applies to exits or `sell-holdings`. It does not pause the bot: that bar's entry is skipped and
recorded in `decisions`. Resupply the flag on every publication to keep the cap. Starting point
for a ~500 USDC fleet of alts: `--max-btc-beta-exposure-fraction 0.6`, with no absolute cap.
Revisit after 30 days of `BTC_BETA_EXPOSURE_EXCEEDED` counts in `decisions`. Backtests do not
apply it.

Shared USDC collateral with manual futures ([ADR 0129](../../docs/decisions/0129-paper-futures-books-and-shared-collateral-risk.md)):
Coinbase counts the USDC spot balance as CFM futures collateral, so futures traded by hand on
the account use the same money live spot bots trade. The gate reads the futures mirror
(`thytrader-operator futures-account`) for every new **live** USD/USDC spot entry:

- futures flat, or no futures (state `idle` / `absent`): nothing changes;
- a stale or failed futures read (`unknown`): entries are denied `FUTURES_COLLATERAL_UNKNOWN`;
- futures positions, initial margin or an open-order hold (`in_use`): entries are denied
  `FUTURES_COLLATERAL_IN_USE`, unless the policy sets
  `--futures-live-spot-collateral-reserve-quote R` (policy quote). Then R must be at least the
  CFM initial margin (USD) x `--futures-peg-haircut` (default 1.25, minimum 1.0), else
  `FUTURES_COLLATERAL_RESERVE_SHORT`, and R is withheld from the venue balance every spot cap
  uses. The two currencies are compared under a declared peg, never added.

It never gates exits, paper books, USDT books or in-kind adoption, and it does not pause bots: the
bar's entry is skipped and recorded in `decisions`. Resupply both flags on every publication to
keep them. `thytrader-operator risk` shows `payload.futures_collateral` (`state`, USD figures,
`reserve_quote`, `effect`).

Paper futures books ([ADR 0129](../../docs/decisions/0129-paper-futures-books-and-shared-collateral-risk.md) §4).
A futures strategy (`instrument.kind: future`, ADR 0128) runs in **paper only**. There is no
futures order path: `start --mode live` of a futures strategy (with `--i-understand-live`;
without it the usual 428 comes first) is HTTP 409 `FUTURES_LIVE_UNSUPPORTED` and creates
nothing, and the CLI refuses `--fee-per-contract` with `--mode live` before any HTTP call.
Portfolio sleeves cannot start futures books (no per-contract fee). Setup and start:

- Publish the policy with `--futures-paper-capital-usd N` (a USD envelope separate from
  `--paper-capital-quote`; unset refuses every futures start with `FUTURES_POLICY_UNSET`) and
  optionally `--futures-daily-loss-limit-fraction F` (futures-scope daily loss; unset uses
  `--daily-loss-limit-fraction`). Resupply them on every publication.
- `start --mode paper --cash USD --maker-fee-rate M --taker-fee-rate T --fee-per-contract F`.
  All three fees are required (`FUTURES_FEE_REQUIRED`); take M and T from
  `thytrader-operator fees` → `payload.futures` and F from `fees --futures-preview-product-id`
  when available. `--cash` beyond the envelope is `FUTURES_PAPER_CAPITAL_EXCEEDED`.
- The start binds the contract from the latest catalog observation and never re-reads it.
  Perp-style contracts only (`FUTURES_PAPER_UNSUPPORTED` for dated ones);
  `FUTURES_CONTRACT_UNOBSERVED` / `FUTURES_UNDERLYING_MISMATCH` name a catalog problem.
- Watch it with `show UUID`: a futures bot adds `futures` (the same object as
  `GET /api/v1/deployments/{id}/futures` and one row of `thytrader-operator futures-books`):
  bound contract, `side`, `contracts`, mark, USD `equity`, `notional`, `leverage`, overnight
  `initial_margin` / `maintenance_margin`, `liquidation_buffer_fraction` vs
  `min_liquidation_buffer_fraction`, `liquidation_price`, the funding ledger,
  `funding_overdue_since`, `entry_blocks` (what denies the next entry now) and `unknown`
  (`null` figures are unknown, never zero). If that read fails `show` returns `futures: null`
  with `futures_error`; do not infer figures. Per-bar outcomes are in `decisions`.
- Stop it like any bot: `stop UUID --confirm` is a managed stop (the protective stop stays and
  the liquidation monitor keeps running); `stop UUID --flatten --confirm` exits the contracts
  at the next priced bar (a short is bought back). `pause` / `resume` work as for spot (paper
  resume needs no `--i-understand-live`). Field reference:
  `skills/thytrader-operator/references/report-schemas.md` (Paper futures books).

Each cycle the book reads the overnight margin rates (never intraday), sizes whole contracts
within the lower of the strategy's and the policy's `max_leverage` and the policy's liquidation
buffer (0.5 while unset), and pays maker/taker plus the per-contract fee on every fill. Funding is charged hourly while a position is held
(longs pay positive rates) at the close of the bar containing the hour, once per hour. New
entries are denied, never paused, while evidence is unknown: `FUTURES_CONTRACT_UNBOUND`,
`FUTURES_MARGIN_UNKNOWN`, `FUNDING_HISTORY_MISSING` (a settled hour still missing 75 minutes
later; it is retried every cycle, never zero-filled), `FUTURES_LEVERAGE_EXCEEDED`,
`FUTURES_LIQUIDATION_BUFFER`. A closed bar whose adverse extreme leaves equity below
maintenance sends a `liquidation`-purpose marketable exit before the stop. Futures books are
their own breaker scope (`CFM-USD`: daily loss against the futures envelope, per-strategy
drawdown); a latched futures daily-loss breaker denies paper USD/USDC spot entries and a latched
paper USD/USDC breaker denies futures entries (`SHARED_COLLATERAL_BREAKER`, naming the latched
scope). Losses are never added across scopes.

Futures gate caps (ADR 0129 §5), all optional on `set-risk-policy` and resupplied on every
publication: `--futures-max-leverage` (1–20; `FUTURES_LEVERAGE_EXCEEDED`),
`--futures-min-liquidation-buffer-fraction` (`FUTURES_LIQUIDATION_BUFFER`),
`--futures-max-exposure-fraction` (gross futures notional over the futures envelope;
`FUTURES_EXPOSURE_EXCEEDED`), `--futures-max-order-contracts`
(`FUTURES_ORDER_CONTRACTS_EXCEEDED`), `--futures-max-hourly-funding-rate-abs` (latest settled
rate; `FUTURES_FUNDING_RATE_EXCEEDED`, or `FUNDING_HISTORY_MISSING` while it is unknown),
`--futures-max-daily-loss-usd` (absolute futures-scope ceiling, paper included) and
`--futures-max-btc-beta-exposure-fraction` (the futures scope's own BTC-beta cap over the
envelope; β of `<underlying>-<policy quote>`; `BTC_BETA_EXPOSURE_EXCEEDED` /
`BTC_BETA_UNAVAILABLE`). `--futures-beta-netting net_by_underlying` lets managed paper futures
offset same-underlying paper spot inventory in base units for the **spot** BTC-beta cap only;
an unbound book, an overdue funding hour or a missing mark falls back to gross, netting only
ever lowers the spot figure, and live is never netted. All are policy limits, not faults.

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

Execution history longevity ([ADR 0113](../../docs/decisions/0113-deploy-anchored-window-cache.md)):
the fixed deployment warmup start does not slide, even after more than 90 days on `1m`.
The worker caches settled history and re-reads the newest/unsettled closed tail on every load;
a missing newest candle is still never fabricated. HTF/extra indicators sharing a clock reuse
only sufficient union coverage, and reference coverage includes lagged and crossover reads.
After restart, credential replacement, or cache eviction, an old book's anchored history may
need several bounded warming passes before decisions can continue. Local cache warming is
**not missing exchange data**, is not a request to resume, and must not clear a deliberate
pause or breaker. Do not change policy, recreate the bot, or queue ingest merely to bypass it.
Check `show`, `decisions`, and operator `runtime` / `reconciliation`; report a persistent stall.
Candle-independent order reconciliation/protection must continue during warming. Each window
load permits eight range calls of at most 350 candles including overlap/confirmation; this is
not a global worker/venue HTTP rate budget. The retained prefix and full indicator computation
still grow with lifetime; this is not constant-memory indicator state.

## Hard stop

When operating a running instance, do not edit `src/`, `compose.yaml`, Dockerfiles, Alembic, or
tests, and do not search the tree for a code patch. Report failures through this skill. Rebuild
only with `make run` when the user asked or the [stale-image rule](../README.md#shared-rules-every-lane)
applies. Run every `uv run thytrader-*` command from the repository root (the parent of `ops/`).

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
`strategy_id: null`, `strategy_deleted: true`, and `strategy_name` kept; stopped paper books and
their referenced snapshots are likewise retained as risk evidence (ADR 0111). Deletion output
`counts.paper_deployments` counts removals and is zero, not a count of retained books.

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
`covered` on every read when inventory economics are resolved, `list` included, so the two
fields agree
([ADR 0098](../../docs/decisions/0098-library-views-book-marks-portfolio-fills.md)). That paper
`covered` is worker-dependent, not a venue-resting order.

`show` and `list` positions, operator runtime `books[]`, and portfolio sleeve `books[]` include
`protection` ([ADR 0112](../../docs/decisions/0112-quantitative-protection-evidence.md)). Read it
before treating `protection_status: covered` or `position_state: open_protected` as a green venue
stop. Live cover requires recent persisted OPEN stop evidence on the closing side, with remaining quantity at
least the book quantity and stop geometry matching the working stop (a bracket target must match
too). A take-profit alone is `unprotected`. Pending and unknown stops are `unknown`, not covered.
`covered_quantity` + `uncovered_quantity` equals `required_quantity`. `verified_at` / `observed_at`
are null when unknown. Paper evidence has `mechanism: synthetic` and `worker_dependent: true`.
Partial fills, pyramid adds, and stale mismatched brackets leave `uncovered_quantity` above zero.
The same attached child is not counted twice; duplicates are folded by venue receipt time, never
local recency, and conflicting or unknown histories fail closed. Actual order kind and executable trigger/limit
geometry matter, not merely the intent purpose. A profitable trailing stop may cross entry;
`geometry_basis` labels `working_target`, `stop_limit_trigger`, or `unknown`.

`observation_source` is `venue_order_state` (live: `Order.venue_observed_at`, set only by a
successful identified venue order read), `persisted_order` (legacy/local-only rows),
`synthetic_worker` (paper), or `none`
([ADR 0119](../../docs/decisions/0119-venue-order-observation-provenance.md)). `observed_at` is the
latest relevant venue receipt and `verified_at` the oldest receipt among contributing fresh OPEN
stops; both are null when unknown, and local writes, migrations, and restarts never refresh them.
`freshness` (`recent_venue` / `stale` / `unknown`) is order-state age against `evaluated_at` and
`freshness_max_age_seconds: 120` (four default worker polls, independent of strategy candles).
Stale, future-dated, undated, or unidentified OPEN rows contribute no covered quantity (reasons
`venue_evidence_stale`, `observation_time_future`, `observation_time_unknown`,
`venue_identity_missing`); local-only evidence carries `local_observation_only`. `covered` plus
`recent_venue` proves fresh order state matching **persisted submitted geometry**, not an
independent venue-geometry audit or a guaranteed stop-limit fill; the UI shows **Order state
fresh** in amber. Slow custom polling may report stale without changing supervision. This
reporting never submits/cancels orders or alters deliberate pauses; never infer mutation authority
from a protection badge.

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
On full `show --detail full`, `ledger.mark_complete`, `marked_exposure`, `total_net_pnl`, and
`total_return_fraction` require resolved inventory/execution economics and every needed product's
journaled close. Missing projection, unpublished/unapplied execution economics, or missing marks
leave dependent totals null. Summary `show` omits retained fill economics: it cannot certify
aggregate accounting or flatness from absent positions. Known local position cover stays visible,
not a whole-account completeness claim. Unknown protection quantities are null, never zero or an
executable sell quantity. Reads never fetch a venue price or reconstruct a position to fill the
gap. Use full detail for retained economics; paged ledgers disclose only their own population.

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

## Holdings already in the account (inventory adoption, live only)

Coins held at Coinbase that no bot manages (bought by hand, left by a retired bot) show as
`external_inventory` in `thytrader-operator venue-reconciliation`. A live book can **adopt**
them: it takes ownership at the mark without buying anything
([ADR 0124](../../docs/decisions/0124-inventory-adoption.md)). Two actions:

- **Protect** keeps the coins and has the platform guard them. They go into a running
  discretionary long book, which rests the stop and take-profit you give exactly as after a
  live entry fill.
- **Sell** converts them to the product's quote currency. They go into a book created
  STOPPED with lifecycle `flatten`, and the execution worker sells them marketably on its next
  cycle. No protective order is ever placed on that book.

The product you name chooses the quote: `DOGE-USDC` sells DOGE into USDC, `DOGE-USD` into USD.

In the browser, Trade's order type **Adopt holdings** protects held coins, and each Home
holdings row offers **Sell to USDC** and **Adopt into bot**. All of them go through a live
confirmation; agents use the commands below.

Always run the read-only preview first:

```bash
uv run thytrader-runtime adoption-preview --product-id DOGE-USDC
```

**Preview fields.**

| Field | Meaning |
|---|---|
| `balance_total` / `balance_available` | The venue's base balance. `available` excludes base on hold under resting orders. |
| `claims` | What live books already own or will own of the base: `managed_long`, unfilled opening `working_buys`, and `working_short_entry_sells`. |
| `adoptable` | `min(available, total − claims)`, rounded down to the base increment. This is the most `--quantity all` can take. |
| `mark` / `mark_bar_starts_at` | The close of the last closed candle on `--timeframe` (default `5m`). It is the adoption price and the price your stop and take-profit are checked against. For a thinly traded coin whose latest 5m bar is stale (`ADOPTION_MARK_UNAVAILABLE`), use `--timeframe 1h`. The book keeps that clock, so a sell waits for a traded bar on it. |
| `protect_blocking_reasons` / `sell_blocking_reasons` | Empty means that action can proceed. |

A null figure is unknown, never zero. `unresolved_reasons` (an UNKNOWN order, unsettled fills,
unresolved accounting, or a missing or duplicate balance row) refuses both actions with
`ADOPTION_BASE_UNRESOLVED` until reconciliation settles.

**Protect:**

```bash
uv run thytrader-runtime place-order --mode live --entry-kind adopt --product-id DOGE-USDC \
  --quantity all --stop-price 0.15 --take-profit-price 0.30 --idempotency-key KEY \
  --confirm --i-understand-live
```

- `--quantity` is a base amount or `all`. The stop must be below the mark and the take-profit
  above it; both are required.
- Adopt never buys, so `--side short`, `--quote-notional`, `--limit-price`, `--cash` and the fee
  flags are refused.
- It reuses a flat running discretionary book for the product. It refuses with
  `ADOPTION_BOOK_OCCUPIED` while a discretionary book on that product is open, pending or paused.
- The entry gate admits it **in kind**: the adopted notional joins live capital, and order
  bounds, rate limits and the price collar do not apply. Allowlist, allocations (discretionary
  books are denied while allocations are in force), slots, exposure caps and the daily-loss and
  drawdown breakers do apply.
- It is allowed under fleet disarm, because it only adds protection.

**Sell:**

```bash
uv run thytrader-runtime sell-holdings --product-id DOGE-USDC --quantity all \
  --idempotency-key KEY --confirm --i-understand-live
```

- It is not entry-gated by the risk policy (it reduces risk) and is allowed under fleet disarm.
- The response is the new book: `status: stopped`, `lifecycle_command: flatten`, an open
  position, and a sentinel stop of one price increment that is never placed.
- Follow it with `uv run thytrader-runtime show UUID` (or
  `thytrader-operator runtime --deployment-id UUID`) until it is flat. The sale's fills reconcile
  like any live exit.
- If no traded closed candle is available, the detail says flatten is waiting for a price; the
  next traded bar sells.

**Start a strategy bot already holding them:**

```bash
uv run thytrader-runtime start --strategy-id UUID --mode live --adopt-holdings all \
  --confirm --i-understand-live
```

- The live bot starts OPEN with the adopted coins and buys nothing. The book and the
  adoption commit together: if the adoption is refused, no bot exists. Live only; v1 accepts
  single-instrument, long-side strategies (otherwise `ADOPTION_STRATEGY_UNSUPPORTED`).
- The mark is the newest closed bar of the strategy's own clock. The stop and target come from
  the strategy's exits at that mark, using the initial-stop ATR of the same deploy-anchored
  window the worker evaluates. There is no target when the strategy declares no take-profit.
  An undefined ATR or illegal geometry is refused with `ADOPTION_LEVELS_UNAVAILABLE`, and a
  missing or stale bar with `ADOPTION_MARK_UNAVAILABLE`.
- The worker's next cycle rests the protection. From the first bar after the adoption bar,
  the strategy's exits manage the position: stop, trail, take-profit, signal exit and time exit.
- Admission is the normal live start (published policy, allowlist, allocations, slots), then
  the entry gate in kind. With allocations in force, the strategy's allocation must cover the
  adopted notional (`ALLOCATION_EXCEEDED` otherwise). Performance capital is max(allocation,
  adopted notional).
- A disarmed fleet refuses it like any live start (`ENTRY_INHIBITED`). `--confirm` is always
  required; YOLO never skips it.

**Both actions:**

- Repeating the same `--idempotency-key` returns the original book and never adopts twice.
  After a timeout, `show` the book and repeat the identical command.
- A quantity above `adoptable` is refused with `ADOPTION_QUANTITY_UNAVAILABLE`.
- A lot below the venue's base or quote minimum is refused with `ADOPTION_BELOW_VENUE_MINIMUM`.
- Paper answers `ADOPTION_LIVE_ONLY`.
- Each adoption writes one why-trade record (purpose and signal kind `adoption`) and an
  `inventory_adopted` audit event with the mark, the balance and the claims.
- Adopted losses count toward the daily-loss and drawdown breakers.
- Execution-quality reports flag adopted fills `adopted: true`.

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
| List deployments (complete stable snapshot) | `uv run thytrader-runtime list` |
| One inventory page (`has_more` is explicit) | `uv run thytrader-runtime list --limit 50 --offset 0` |
| Show summary (labels omitted history) | `uv run thytrader-runtime show UUID` |
| Show full orders and fills | `uv run thytrader-runtime show UUID --detail full` |
| Page orders or fills | `uv run thytrader-runtime orders UUID` / `fills UUID` |
| Preview a fleet action | `uv run thytrader-runtime fleet-preview --action disarm --mode paper` |
| Disarm entries (no flatten) | `uv run thytrader-runtime fleet-disarm --mode paper --idempotency-key KEY --expect-inhibition paper:REV --confirm` |
| Managed-stop confirmed ids | `uv run thytrader-runtime fleet-stop --mode paper --idempotency-key KEY --expect ID:REV --confirm` |
| Explicit flatten confirmed ids | `uv run thytrader-runtime fleet-flatten --mode live --idempotency-key KEY --expect ID:REV --confirm --i-understand-live` |
| Rearm after disarm | `uv run thytrader-runtime fleet-rearm --mode live --idempotency-key KEY --expect-inhibition live:REV --confirm --i-understand-live` |
| Per-bar decisions of one bot (read-only) | `uv run thytrader-runtime decisions UUID [--outcome no_signal] [--limit 50] [--cursor C]` |
| Decisions across a strategy's bots | `uv run thytrader-runtime decisions --strategy-id UUID [DEPLOYMENT_UUID] [--outcome entry_signal --outcome exit]` |
| Start paper | `uv run thytrader-runtime start --strategy-id UUID --mode paper --cash 10000 --confirm` |
| Start paper with operator-chosen fee rates (omit them to use the account's rates) | `uv run thytrader-runtime start --strategy-id UUID --mode paper --cash 10000 --maker-fee-rate 0.001 --taker-fee-rate 0.002 --confirm` |
| Start live | `uv run thytrader-runtime start --strategy-id UUID --mode live --confirm --i-understand-live` |
| Start a paper futures book | `uv run thytrader-runtime start --strategy-id UUID --mode paper --cash 5000 --maker-fee-rate 0 --taker-fee-rate 0.0005 --fee-per-contract 0.15 --confirm` |
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
| Preview held coins (read-only) | `uv run thytrader-runtime adoption-preview --product-id DOGE-USDC [--timeframe 5m]` |
| Protect held coins (adopt, live) | `uv run thytrader-runtime place-order --mode live --entry-kind adopt --product-id DOGE-USDC --quantity all --stop-price 0.15 --take-profit-price 0.30 --idempotency-key KEY --confirm --i-understand-live` |
| Sell held coins (live) | `uv run thytrader-runtime sell-holdings --product-id DOGE-USDC --quantity all --idempotency-key KEY --confirm --i-understand-live` |
| Start a live bot holding held coins | `uv run thytrader-runtime start --strategy-id UUID --mode live --adopt-holdings all --confirm --i-understand-live` |
| Show risk policy | `uv run thytrader-runtime show-risk-policy` |
| Publish risk policy | `uv run thytrader-runtime set-risk-policy --quote-currency USDC --product-allowlist BTC-USDC --max-concurrent-running-deployments 8 --max-concurrent-open-positions 8 --max-portfolio-exposure-fraction 1 --per-product-max-exposure-fraction 1 --paper-capital-quote 100000 --confirm` |
| Publish risk policy with pyramiding | `uv run thytrader-runtime set-risk-policy --max-concurrent-running-deployments 8 --max-concurrent-open-positions 8 --max-portfolio-exposure-fraction 1 --per-product-max-exposure-fraction 1 --paper-capital-quote 100000 --allow-intra-strategy-pyramiding --confirm` |
| Publish risk policy with a fleet entry clustering cap | `uv run thytrader-runtime set-risk-policy --max-concurrent-running-deployments 8 --max-concurrent-open-positions 8 --max-portfolio-exposure-fraction 1 --per-product-max-exposure-fraction 1 --paper-capital-quote 100000 --max-fleet-entries-per-window 4 --fleet-entry-window-minutes 120 --confirm` |
| Publish risk policy with a paper futures envelope | `uv run thytrader-runtime set-risk-policy --max-concurrent-running-deployments 8 --max-concurrent-open-positions 8 --max-portfolio-exposure-fraction 1 --per-product-max-exposure-fraction 1 --paper-capital-quote 100000 --futures-paper-capital-usd 20000 --futures-daily-loss-limit-fraction 0.05 --confirm` |
| Publish risk policy with a BTC-beta exposure cap | `uv run thytrader-runtime set-risk-policy --max-concurrent-running-deployments 8 --max-concurrent-open-positions 8 --max-portfolio-exposure-fraction 1 --per-product-max-exposure-fraction 1 --paper-capital-quote 100000 --max-btc-beta-exposure-fraction 0.6 --confirm` |
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

`list` and summary `show` return `positions[]`, `instrument_runtimes[]`, and aggregate
`book_totals`. They do **not** include historical `orders`/`fills`. Summary `show` sets
`ledger_omission` to say so. `show --detail full` includes those collections; otherwise read
`orders` / `fills` pages. A full detail response still returns product-tagged `orders`/`fills`,
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
`high_water_mark_equity`, preserved legacy `utc_day_open_equity`, and optional
`risk_day_open_evidence` (`source`, `day_start`, `equity`, `fills_fingerprint`, product `marks[]`
with `product_id`, `closes_at`, `price`). Legacy opening equity is **not** verified midnight
provenance. Qualified evidence must belong to the observed UTC day and still match complete
current accounting; old preserved evidence alone is not permission to trade.
`capital.inventory_cost`, `reserved_buying_power`, and `performance_equity` are null when report
accounting is incomplete, including bounded summaries. Independent stored funding/budget history
and observed venue quote remain visible; they do not repair projection or certify current equity.
Use operator `readiness` / `venue-reconciliation` to inspect separate read/accounting completeness.
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

`runtime_observability: capital_normalized_performance`
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
([ADR 0099](../../docs/decisions/0099-operand-level-indicator-offsets.md)). Health advertises
`indicator_operand_offset_runtimes` including the deployment mode. These reads lag completed bars on the indicator's own clock, add to declaration offsets,
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
`--taker-fee-rate` together (Decimal strings in `[0, 0.1]`, maker ≤ taker). Omitted, a **new**
paper book (start, each new `portfolio-start` sleeve, or a ticket that creates a book) stores the
account's own Coinbase rates (`suggested_*` from `thytrader-operator fees`, `suggestion_source:
coinbase_account`). If those cannot be read (demo, missing credentials, Coinbase read failure) the
start is refused with a 409 (`paper_fees_unavailable` for portfolios) and nothing is created; do not
retry with invented rates. Pass explicit rates only when the operator chose them
([ADR 0122](../../docs/decisions/0122-paper-fees-default-to-account-rates.md)). A reused ticket book
keeps its stored rates. Paper fills stay modeled, not observed Coinbase fills. Live
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

- `GET /api/v1/deployments?limit=&offset=&as_of=&cursor=` (summary rows; stable `created_at,id`
  order; `has_more`, `returned`, `total`, `order`, `as_of`, `fingerprint`, `next_cursor`.
  Default limit 50 is one page, not the fleet. Follow `next_cursor` with offset 0. A changed
  membership/classification returns 409 `inventory_changed`; restart or report incomplete.
  Legacy offset pages cannot prove completeness. No historical orders/fills;
  `ledger_omission` says so.)
- `GET /api/v1/deployments/{id}?detail=summary|full` (default `summary`; summary sets
  `ledger_omission` and omits historical orders/fills)
- `GET /api/v1/fleet-control` and `GET /api/v1/fleet-control/preview?action=&mode=`
- `POST /api/v1/fleet-control/{disarm|stop|flatten|rearm}` (`confirm: true`; live rearm and
  live-capable flatten also `i_understand_live: true`; disarm/rearm also require
  `expected_inhibition` preview revisions for every scoped mode)
- `GET /api/v1/deployments/{id}/fills?limit=&cursor=` and `/orders?limit=&cursor=`
- `POST /api/v1/deployments` (mode `live` requires `"i_understand_live": true`, else 428; optional
  `adopt_holdings` (a quantity or `"all"`, live only) starts the bot holding adopted coins)
- `POST /api/v1/deployments/{id}/pause`
- `POST /api/v1/deployments/{id}/resume` (live books require body `{"i_understand_live": true}`, else 428)
- `POST /api/v1/deployments/{id}/stop` (optional `?flatten=true`; default is managed shutdown)
- `POST /api/v1/deployments/{id}/reset-breaker-latches`
- `POST /api/v1/discretionary-orders` (mode `live` requires `"i_understand_live": true`, else 428)
- `GET /api/v1/inventory-adoptions/preview?product_id=&timeframe=` (read-only)
- `POST /api/v1/inventory-adoptions` (`mode: live`, `action: protect|sell`, `quantity` decimal or
  `"all"`, `stop_price` and `take_profit_price` for protect only, `idempotency_key`, `origin`,
  optional `timeframe` and `note`; requires `"i_understand_live": true`, else 428; paper 409
  `ADOPTION_LIVE_ONLY`; refusals 409 with the code first; shape errors 422; replay returns
  the original book with 201)
- `GET/PUT /api/v1/risk-policy`
- `GET/PUT /api/v1/settings` (YAML non-secrets and YOLO; no secret echo; [ADR 0055](../../docs/decisions/0055-yaml-settings-runtime-reloadable-yolo.md))
- `GET/PUT/DELETE /api/v1/credentials/coinbase`

## Confirmation

- Never mutate unless the user explicitly asked **and** `--confirm` is present, unless the user
  explicitly asked to operate under YOLO **and** operator `configuration` /
  `thytrader-playbook status` shows the matching tier (`paper` or `live`) enabled.
- Never start live, resume a live deployment, place a live order, adopt held coins, or sell
  holdings without `--i-understand-live`. YOLO never skips that flag. Live start/pause/resume/stop
  may omit `--confirm` only when the `live` tier is enabled and the skip audit succeeds. Live
  `place-order` (including `--entry-kind adopt`), `sell-holdings`, `set-risk-policy`,
  `set-settings`, and Coinbase credential set/clear never YOLO.
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

`runtime_observability: explicit_deployment_twins`
([ADR 0102](../../docs/decisions/0102-explicit-paper-live-twin-links.md)). Run from the repository root:

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

`runtime_observability: rule_matched_deployment_twins`
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

Between-bar, data-gap, and warming supervision use each product's own runtime, inventory,
venue increments, and verified context — never the compatibility-only singular `position`.
Warming may finish an already recorded live signal exit, reached time exit, or flatten; it
must not replace that decision with a new bracket or advance the strategy decision cursor.
A deliberate pause stays paused.

A canceled order's reported executions must be fully represented by applied REST fills
before another cover or flat settlement. Empty or partially published fills remain a wait
across restart; `canceled` does not mean its executed quantity was zero. If bought/sold
inventory has applied cash/fee economics but could not acquire position metadata, retained
entry orders and fills keep it unresolved even if `mismatch_detail` later changes. Protection
is kept; no stop geometry or sell quantity is invented. A missing position row is not proof
of flatness. This also applies to an `OPEN` / `PENDING_EXIT` product runtime whose own position
is absent while another product remains projected, in running, paused and stopped books.
Read `show UUID` and `thytrader-operator reconciliation`, report the projection
fault, and do not clear it by resuming, restarting, or editing storage/recorded fills.

`portfolio-status --portfolio-id ID` qualifies current accounting separately from lifecycle
state. Sleeve, breaker and exposure `accounting_complete` flags and breaker/exposure
`unresolved_deployment_ids` disclose uncertainty. Dependent equity/PnL/exposure/return/drawdown
are null, not free capacity; stored limits/allocations/baselines and independent projected books
remain visible. Run performance includes stopped current-run books, while exposure also qualifies
older occupied/residual books. Missing sleeve reads have `open_books: null`. These reporting
flags grant no mutation authority and do not reset a breaker or override a deliberate pause.

## Fleet controls (ADR 0117)

`fleet-preview` is read-only. It lists affected deployment ids, current revisions, and residual
positions. Unknown position reads stay unknown; do not treat them as flat. Disarm, managed stop,
and flatten are different commands:

- `fleet-disarm` inhibits new starts and entries for `--mode paper|live|all` until `fleet-rearm`.
  It does not pause, cancel, or flatten. It does not need `--i-understand-live`.
- `fleet-stop` records managed shutdown only for each `--expect ID:REVISION` from the preview.
  Protection stays. This is not a flatten. Repeat the same `--idempotency-key` after a timeout.
- `fleet-flatten` is explicit. Live scope also needs `--i-understand-live`. Acceptance means the
  lifecycle command was recorded. The worker exits asynchronously. A partial `status` means at
  least one confirmed book was not commanded. Do not claim positions are flat.
- `fleet-rearm` clears the latch and does not resume books. Live scope needs `--i-understand-live`.
  A later live `resume` still needs its own acknowledgement.

YOLO never covers these commands. `--confirm` is always required. A changed revision returns
`revision_conflict` for that id and does not apply the stale preview.

Disarm/rearm must also confirm latch revisions from `fleet-preview.inhibition`. Pass
`--expect-inhibition paper:N` for paper, `live:N` for live, and **both** for `--mode all`.
Do not automatically fetch newer revisions to replace the person's consent. An unchanged but
newly confirmed disarm still advances its revision, so an earlier rearm preview cannot clear it.
After a timeout, repeat the **identical** action, mode, expected ids/latch revisions, acknowledgements,
and idempotency key. A changed request for that key is rejected. A result's `inhibition` is the
snapshot in its causal receipt, not proof of current latch state; `fleet-status` reads current state.
Unknown errors leave pending progress, never accepted venue work. Receipts survive restarts and
are coupled to the latch or revision-guarded lifecycle write; replay never guesses from coincidental
current state.

Disarm linearizes against the admission commit on the mode latch row. Entry intents and open orders
accepted before it may remain in flight: disarm does not cancel them. Missing durable state or a
worker not yet refreshed at boot inhibits new entries, without stopping reconciliation/protection.

`list` / `list --all` follows checked keyset cursors and publishes only a complete walk. For a
manual page use `list --limit 50`, then `list --limit 50 --cursor CURSOR` from `next_cursor`.
`list --limit 50 --offset 50` is a legacy page; `complete: false` even at the tail. Membership changes,
missing/repeated cursors, duplicate ids, or inconsistent totals cause an incomplete error. Restart
from page one; never present a returned prefix as the full fleet.
