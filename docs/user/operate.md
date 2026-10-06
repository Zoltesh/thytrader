# Operate ThyTrader

You can drive ThyTrader from the browser, from confirmation-gated CLIs, or both. An authorized
agent can do the same loop **100%** through the shipped skills. Nothing requires an agent.

When an agent is operating a **running** instance, open [`ops/`](../../ops/README.md) rather than
the git root. Skills of record: [`skills/README.md`](../../skills/README.md).

## Live performance accounting

Bot detail and `uv run thytrader-operator performance --deployment-id UUID` report return and
maximum drawdown against the bot's pinned performance capital. Read the budget with
`uv run thytrader-runtime show UUID`: `capital.performance_capital_quote` is fixed when the
bot starts with an allocation, or at its first known positive sizing balance when unallocated.
Existing books adopt their recorded opening balance or first verified budget after upgrade.
Later rebalances change sizing without resetting performance percentages.

Ledger equity remains trading PnL for a zero-based live bot. For example, a 100-quote budget
and a 5.5-quote loss (including paid fees) mean −5.5% return and 5.5% drawdown before any profit.
`capital.performance_maximum_drawdown_fraction` retains the worst observed fraction through
recovery and restart. It combines fill-event and worker observations, not a complete historical
candle equity curve. Unknown capital or missing marks show unknown percentage metrics and
block new risk. Breaker reset clears the latch without erasing the peak or performance history;
it leaves the bot paused and a continuing breach can trip again.

See [ADR 0107](../decisions/0107-capital-normalized-live-performance.md). The strategy drawdown
breaker measures the current loss from its durable peak; account daily-loss and exposure limits
continue to use their separate account capital.

## Account and reconciliation diagnostics

Run `uv run thytrader-operator health`, then `uv run thytrader-operator exchange` for
an exchange failure. `payload.failure` names the failed read (`balances`, `permissions`,
`price`, or `fees`), its category (`http`, `timeout`, `network`, `invalid_response`),
and an HTTP status when available. It omits provider response text and credentials.
A price read can fail while the order feed stays connected; inspect both reports.
An unclassified failure remains failed, with `failure: null`.

`uv run thytrader-operator reconciliation` lists individual audit failures from the
newest 20 audit events. Each `audit_event` supplies the event identity, UTC time, action,
provider/product, and recovery evidence. A `recovered` WebSocket failure has a matching
later connected event, identified by `recovery_event_id` and `recovered_at`. Check the
current feed with `uv run thytrader-operator runtime`; a past recovery is not current
health. `unresolved` means no matching recovery was observed in this window, and `unknown`
means there is no recovery rule for the action. A later connection never proves an
ambiguous order succeeded. Recovered failures remain visible and degraded in this
window; historical audit records are retained.

## Durable safety alerts

Read `uv run thytrader-operator alerts` (HTTP by default),
`GET /api/v1/operator/alerts`, or **System → Alerts** (`/alerts`). The feed retains
open and recently resolved book failures, breaker/mismatch pauses, protection
problems, decision deadlines, and unknown/stale worker-lease evidence. It never
places an order or changes a deployment. Counts and health use all open alerts;
the bounded display prioritizes critical rows and warns when it is truncated.

An alert resolves only after its own condition is rechecked with complete evidence.
Unavailable/partial snapshots, missing or warming candle data, storage failures,
and subset inventories are **unknown, not recovery**. A consumed live stop stays
open through a price rebound until durable terminal/fill/removal evidence clears
that order; alerts never turn it into a market order. An implausibly future lease
means unknown age/possible clock skew, not proof that protection maintenance ran.

Repeated failures without verified recovery can pause new entries using a fenced
book write. User pauses, stop commands, latches, and unrelated mismatches remain
intact. A persisted supervision pause survives restart; reconciliation and exits
continue. After review, control belongs to the separate confirmation-gated runtime
lane, not the read-only operator lane. No automatic resume is performed.

Alerts are durable even with `notify_provider=none`: delivery is explicitly
skipped/disabled and no destination is invented. Optional delivery runs outside
trading cycles with bounded retries. Webhook receivers should deduplicate on the
stable alert UUID: a crash after send but before acknowledgement can cause a retry;
bounded retries can also exhaust without delivery, so external receipt is not
guaranteed. See
[ADR 0115](../decisions/0115-durable-safety-alerts-and-supervision.md) and the
[operator skill](../../skills/thytrader-operator/SKILL.md).

## In the browser

After [setup](setup.md), open http://127.0.0.1:5175.

The first usable slice shows deterministic demo balances when Coinbase credentials are empty, and
live balances when both Coinbase variables are configured. That screen never submits an order.

### Finding your way around

The left rail has four destinations ([ADR 0079](../decisions/0079-four-destination-shell-agent-panel-palette-tokens.md)):

| Rail item | Opens | Also holds |
| --- | --- | --- |
| **Home** | `/`: balances, live exposure, what needs attention, your bots | Data health disclosure (`/#data-health`) |
| **Strategies** | `/strategies`: library | Each strategy's workspace: Build `/strategies/{id}`, Test `/test`, Run `/run`, Why `/why`. Old `/research`, `/backtests`, `/deploy` links redirect there |
| **Portfolio** | `/deployments`: every paper and live bot, grouped by state | `/deployments/{id}` bot detail |
| **Trade** | `/trade`: on-demand order ticket with a Review aside | |

**System**, at the bottom of the rail, expands to Settings (`/settings`), Audit log (`/audit`),
Journal (`/journals`), and Memory & why-trade (`/memory`). Every route above still works as a
direct link. The top bar shows where you are (`section / page`).

- Press **⌘K** (macOS) or **Ctrl+K**, or click the search box, to open the command palette. It
  jumps to any page, opens the agent, or takes you to **New strategy** (the library's create
  controls). It only navigates. It never creates, deploys, or orders anything.
- The **Agent** button opens the operator chat in a right-side panel on any page. The panel shows
  what you are looking at, and remembers whether you left it open. The same chat is also at
  `/chat` as a full page.
- The moon/sun button switches between dark and light themes. Your choice is kept in this browser.
  Until you choose, the app follows your operating system's setting.
- **Live chrome.** Whenever a page shows live exposure or is composing a live order, an amber strip
  under the top bar reads `LIVE: …` next to a warning icon (for example "this bot places real
  Coinbase orders · ETH / USDC · allocated 100.00 USDC"), and an amber frame outlines the page.
  It appears on a live bot's detail page, on Trade while the mode is **Live**, and on a strategy's
  Run stage while the **Arm live trading** dialog is open. It never appears for paper-only views.
  The text always says LIVE, so it does not rely on color.

### Home

Home (http://127.0.0.1:5175/, [ADR 0084](../decisions/0084-home-kpis-needs-attention-data-health.md))
answers "how am I doing, and what needs me?" from existing endpoints only. Every card loads on its
own with a skeleton and its own error and **Retry**, so a slow source (the data catalog can take
about 20 seconds; the Coinbase portfolio call is slow) never blocks the rest. A figure that cannot
be known shows `—` with the reason, never a guess. Home summarises live bots but is not a live
context: it shows no amber strip; live rows and items carry a **LIVE** tag.

- **Header.** One line with the Coinbase connection, every detected permission (for example
  `View + Trade + Transfer`; extra permissions are reported, never treated as consent), and how old
  the last balance snapshot is. **New order** opens Trade; **New strategy** opens Strategies.
  Without Coinbase credentials a **Demo data** banner says the balances are a deterministic demo; a
  fresh install with no balances shows the getting-started path instead.
- **Portfolio value.** The newest of the current Coinbase reading and the newest snapshot, with its
  change since the oldest snapshot in the last 24 hours ("since HH:MM" when history is shorter). A
  reading more than 10 minutes old says so. Demo balances are labelled demo and show no change:
  history records live balances only.
- **Available to trade.** The Coinbase available balance in the installation quote currency (the
  risk policy's `quote_currency`, for example USDC), and **Reserved by live bots**: the
  `allocated_capital` of running and paused live bots that trade in that currency.
- **Live exposure.** Gross marked exposure of running and paused live bots per quote currency, the
  live bot count, and whether open books have exit cover (`flat`, `protected`, `unprotected`, or
  `protection unconfirmed`). Unknown when an open book has no complete mark.
- **Bots.** Running count, paused count, and how many bots appear in Needs attention.
- **Portfolio value chart.** `1D`, `1W`, `1M`, `3M` over portfolio history (`3M` reads the all-time
  range and keeps the last 90 days). Gaps stay visible; see
  [setup](setup.md#portfolio-snapshots-and-home).
- **Needs attention.** One list, critical and live items first. Each item names the problem in
  words beside an icon and links to where it is fixed:
  - paused bots, bots reporting a mismatch (with its detail), and any status other than running,
    paused, or stopped; **Review** opens the bot;
  - tripped daily-loss or drawdown breaker latches (reset them on the bot page);
  - live positions without venue-visible exit cover, or with cover not yet reconciled;
  - only while live bots run or are paused: no Coinbase credentials (**Add credentials** opens
    Settings) or no published risk policy (publish one with `thytrader-runtime set-risk-policy
    --confirm`, or ask the agent);
  - watched datasets whose latest ingest failed, whose backfill is stuck (its scheduled worker
    attempt is overdue, an attempt has run for over an hour, or it keeps failing), that are stale,
    or that have gaps. Each links to the strategy trading that market and clock, else to Data
    health;
  - the newest research job of one of the 12 most recently updated strategies, when it failed in
    the last week (**Open Test**).

  Sources still loading, partly read, or failed are listed under the items with their own Retry, so
  an empty list only says "Nothing needs you right now" after every source was read.
- **Your bots.** Running and paused bots, live and paper, with mode, market and clock, position
  and protection, PnL, and status; each row opens `/deployments/{id}`. Stopped bots stay on
  Portfolio.
- **Holdings.** Your balances, largest first; click a column header to sort (ascending,
  descending, unsorted). The ten largest show by default, with **Show all**. Balances under $0.10
  collapse into one expandable line. **Refresh balances** re-reads Coinbase; a failed refresh keeps
  the last snapshot with the redacted error.
- **Fee tier.** One line: tier, maker and taker rates, 30-day volume, and when Coinbase reported it.
- **Data health.** A disclosure at the bottom (open it directly with `/#data-health`): watched
  datasets with coverage, newest candle, watch and worker state, and the same problem words as
  Needs attention, plus the per-product **Data-source diagnostics**, which load only when opened.

### Strategies

Open http://127.0.0.1:5175/strategies when the stack is healthy. The library requests one
server page at a time (10 rows by default; select 10, 25, 50, or 100). Use Next/Previous to
navigate cursor pages. Changing the page size returns to page one. A failed page shows a retryable
error rather than an empty library. Other selection screens may still load the full library.
Each row shows a checkbox, the strategy name, market (`BTC / USDC`) and clock, whether the saved
definition is valid, a progress pipeline, the latest backtest, and when it was updated. There are no
versions, drafts, or publish steps: a strategy is one object you edit and save
([ADR 0082](../decisions/0082-strategy-root-mutable-strategies-auto-snapshots.md)). The pipeline chips
are evidence, not readiness: **Build** (the saved definition is valid), **Test** (a backtest exists),
**Paper** and **Live** (newest deployment status per mode: running, paused, stopped, or not
deployed). Clicking a row opens that strategy's workspace; the latest-backtest link opens that
result on its Test stage. The **Mine / Research / All** control above the table chooses whose
strategies you see ([ADR 0098](../decisions/0098-library-views-book-marks-portfolio-fills.md)).
**Research** holds strategies that agent research created, meaning anything tagged
`claude-research` or `research-*`. **Mine** holds everything else. The library opens on **Mine**,
so a burst of research never buries your own strategies, and it remembers your last choice in this
browser. A strategy's `metadata.tags` show as chips under its name (research tags in blue). Click
one to show only strategies with that tag within the current view; the filter chip above the table
clears it. Tag strategies you
create in bulk, then list them with `thytrader-research list-strategies --tag TAG` (add
`--origin research` or `--origin operator` for the same split as the page) or remove them
with `bulk-delete-strategies --tag TAG --dry-run` and then `--confirm`
([ADR 0094](../decisions/0094-research-honesty-and-agent-ergonomics.md)). Cross-market variants
are snapshots of their base strategy and never appear as separate rows.

From the library you can create a strategy from a template on any **Market** (a Coinbase spot product such as `BTC-USDC` or `BTC-USD`; the field remembers your last choice and starts at `BTC-USDC`) and **Clock**. Product and timeframe stay editable later under Build → Market and data. You can also **Clone** a strategy into a new
identity, **Import** a strategy definition JSON as a new strategy (older exports that still carry
`version` / `status` import fine; those keys are ignored), and **Delete** strategies. Tick rows (or
the header checkbox to select every row on the page) and choose **Delete N strategies…**; a single
row's delete does the same for one strategy. The confirmation dialog (Cancel is the default focus;
Escape cancels) lists what will be deleted for each strategy — its backtests, studies, research jobs,
and paper bots with their history. A strategy with a running or paused bot is **blocked** and the
dialog says why: stop the bot first. Stopped **live** bots are kept with their orders, fills,
positions, and trade reasons; they stay on Portfolio as "<name> (deleted strategy)". If a risk-policy
allocation names a deleted strategy, the next policy version is published without it. Deletion is
permanent. After confirming, each strategy reports its own result, so a partial failure shows which
strategies were deleted and which were not. Browser writes first establish a CSRF session and send its matching token and cookie;
the app handles this automatically. A 401 CSRF error is a browser-session/client failure, not a
strategy-validation error. Do not disable the trust boundary to work around it.

### Strategy workspace

Every strategy has one workspace ([ADR 0080](../decisions/0080-per-strategy-workspace-build-test-run-why.md)).
A sticky identity bar shows the name, the market with its Coinbase product id, the clock, whether the
saved definition is valid, and the save state (saved, or unsaved changes). **Clone** copies the
strategy into a new identity. The stage links are **Build · Test · Run · Why**. Old links that
carried `?version=` or a strategy fingerprint still open: they resolve the owning strategy and land
on its workspace.

**Build** (`/strategies/{strategy_id}`) edits the strategy in place. Rules read as IF / AND / OR rows
in a nested ALL/ANY/NOT tree; the right column shows **In plain English**, **Checks** (validation,
warmup and required data, a collapsible **How backtests simulate** disclosure), save state, and **Save**. You may save an
incomplete or invalid strategy; Build shows its validation issues, and Test and Run refuse to start
until the saved definition is valid. Saves carry a revision and reject a stale browser tab rather
than overwriting newer edits (reload to see the newer version); leaving Build with unsaved edits
asks first. Saving never changes an existing backtest or a running bot.

**Indicators.** The **Kind** picker groups the 53 indicators by Trend, Momentum, Volatility, Volume,
Statistical, and Price; type to search (for example `hull`, `sar`, or `z-score`). Choosing a kind
fills its parameters with defaults, and each field shows its allowed range and a one-line hint.
**Offset (bars ago)** reads the indicator's value from that many completed bars earlier, so a
breakout can compare the close with the *previous* bar's 20-bar high; leave it empty for the current
bar ([ADR 0086](../decisions/0086-indicator-catalog-expansion-and-offset.md)). Conditions list each
multi-series indicator's outputs under its name, for example `Supertrend(10, 3) · direction`.
**Position sizing** labels use the product's quote currency (`Minimum USDC notional` for a USDC
market). New templates in the library — **Donchian breakout**, **Supertrend trend**, **Squeeze
breakout**, and **Z-score mean reversion** — start from these kinds.

Each indicator side of a condition has its own **Left/Right operand offset (bars ago)** field
(0–500). Zero reads the current value; one reads the previous completed bar on that indicator's
clock, so a daily indicator's offset is in daily bars even when the strategy decides hourly.
This adds to any indicator declaration offset. Use the same indicator with different operand
offsets to compare current and prior values; the new squeeze template does this so sweeping its
band/channel parameters updates both reads together. Increase warmup to cover the longest lag;
Build's checks include entry and signal-exit reads. Backtests and bots use the same interpretation
([ADR 0099](../decisions/0099-operand-level-indicator-offsets.md)).

Saved-strategy summaries show the combined lag on each entry and signal-exit operand:
declaration offset 2 plus operand offset 3 reads `(5 bars ago)`. A current read has no lag suffix;
reference reads retain their native clock, for example `BTC · SMA(2) (3 bars ago) [1d]`.

**Snapshots.** Starting a backtest, study, or bot takes an automatic snapshot of the saved rules
(a `sha256:` fingerprint). Every result and bot row shows **Current rules** when it used the rules
you have now, or **Earlier edit** when the strategy changed since; **What changed** shows a
field-by-field diff between that snapshot and the current definition.

### Test (research and backtests)

Open a strategy's **Test** stage (`/strategies/{strategy_id}/test`; old `/research?strategy=` links
redirect here, and `/research` alone points you to the library). The run bar is one compact row that
starts from the strategy's current saved definition: verified dataset, period (**Full coverage** unless you
set custom dates), initial capital, and maker/taker fees, with **Run a study** and **Run
backtest** on the right. There is no engine to pick: every backtest uses ThyTrader's one backtest
model ([ADR 0083](../decisions/0083-unified-backtest-model.md)), described by **How backtests
simulate** and by `GET /api/v1/research/backtest-model`. Upgrading to this model (migration `0049`) deleted every earlier
backtest result, research run, research job, and study; strategies, snapshots, and bots are kept. **Advanced options** holds fixed slippage,
an optional **Spread stress (bps)** (a disclosed constant bid-ask spread stress, default 0), and
custom evaluation dates; its summary line always shows the current slippage, spread, and
period. Omitting
both evaluation dates on submit uses the common LTF+HTF (and extra-clock) covered intersection
rather than the LTF range alone. When Coinbase credentials are present, maker/taker fields prefill
from your account's reported Coinbase rates (what live fills are billed at) and stay editable; the
public fee-schedule band for the same volume is shown only as context on hover. Demo or missing
credentials leave those fields blank rather than inventing a tier. An opened result has a **Why so
few trades?** disclosure (open by default when a result has no trades): signals matched → entries
rested → filled, entries that expired or were still resting at the end, and how many matched
signals were skipped and why — for example *Short take-profit would be at or below zero*
(`target_not_positive`) or *Already in a position* — plus how positions closed (stop loss, take
profit, time exit, signal exit, evaluation end). Results saved before this was recorded say so;
re-run the backtest to record it. Dataset and result-list failures remain visible without hiding
strategy evidence. Result summaries report the snapshot's strategy clock,
including `2h` and `4h`, not a hardcoded `1h`. **Run a study** opens the composed-study builder
(OOS holdout, walk-forward, parameter sweep, walk-forward optimization) for the same strategy. Below
the run bar, **Results for this strategy** lists every result, each marked **Current rules** or
**Earlier edit**; opening one shows it inline (`?result=` deep link): a compact header (rules · period · time and a
**Simulated result (candle-based fills)** chip), a metrics row (net return, buy & hold, max
drawdown, trades, win rate, profit factor), the evaluated window right under it (*Evaluated
2021-03-02 → 2026-02-28 · 1825 × 1d bars · after a 60-bar warmup*; omitted dates start after each
strategy's own warmup, so set custom dates before comparing two strategies), the equity curve, the
modeled assumptions line, and a
collapsed **Evidence** row with the result, strategy, dataset, and run fingerprints. Ratio metrics,
the buy-and-hold comparison, and the modeled trade ledger follow. Results are research evidence, not
a promise, and there is no Deploy or Start-paper button on them.

Starting a backtest never starts paper or live trading. Backtests require a verified dataset
fingerprint and remain deterministic research artifacts.

**Missing data.** Test and Run list every clock the strategy reads (execution timeframe, the
optional higher-timeframe filter, and any per-indicator timeframe). When a clock has no verified
dataset, or its newest bar is stale, the panel names it exactly (for example
"No verified BTC-USDC × 2h dataset yet"). **Download data…** asks for confirmation, then adds the
product × timeframe to the watchlist at that timeframe's research ceiling (90 days at 1m, 1 year
at 5m, 2 years at 15m, 3 years at 30m, 5 years at 1h, 10 years at 2h and slower; a longer
existing lookback is kept) and queues a no-wait ingest through the same data-lane endpoints as
`thytrader-data watch-add` / `ingest`. The worker fetches newest bars first, up to 350 per request,
so a dataset ending at the latest bar appears quickly and then grows back in time. Coinbase may hold
less history than the ceiling; coverage then starts where its history does. Progress shows
received versus expected candles and refreshes the dataset choices when the watch completes. For the higher-timeframe filter or an extra
indicator clock, a **Change or remove … in Build** link opens the Build stage at that section; the
higher-timeframe filter is optional and off by default.

### Backtests

Open `/backtests` to inspect immutable result summaries across all strategies. An old
`/backtests?result=` or `/backtests?strategy_fingerprint=` link opens the owning strategy's Test
stage (the fingerprint resolves its owning strategy); otherwise the standalone view stays. The list requests 10 newest-first rows
by default; its Rows per page selector offers 10, 25, 50, and 100. Newer/Older request only the
current server page, and changing the size restarts at the newest results. A full page does not
by itself imply there are older results: the server's `has_more` indicates that. Opening an
individual result does not require loading every list page.

### Paper and live

Open a strategy's **Run** stage (`/strategies/{strategy_id}/run`; old `/deploy?strategy=` links
redirect here). A **Paper** card and a **Live** card sit side by side, each listing this strategy's
deployments with status, instruction, entry eligibility, fill-ledger performance,
exposure and protection, a **Current rules** / **Earlier edit** marker, and an **Open bot →** link
to `/deployments/{id}`. **Start paper deployment…** asks for confirmation and starts a new
deployment from the current saved definition (never a promotion of a backtest). A bot on an
earlier edit keeps running those rules; **Update bot…** moves it to the current rules by a managed
stop followed by a new start, confirming each step (live keeps the understand-live checkbox). Pause, resume, stop, and flatten
use the lifecycle dialog. **Arm live trading…** opens a dialog whose confirm stays disabled until
you tick "I understand this places real orders on Coinbase with real money"; live resume asks the
same. The Live card's preflight lists, independently, Coinbase credential presence, whether a risk
policy is published, the available balance in the strategy's quote currency, whether the policy
allocates capital to this strategy, and the clock / user-order feed. Any source that cannot be read
shows **Unknown**. It is not a readiness verdict and does not gate arming; paper results are shown
as information only.

The execution worker
evaluates paper and live deployments against closed venue candles about every 30 seconds.
Paper simulates maker fills; live places Coinbase Advanced Trade spot orders when credentials
exist (credentials set from Settings reach the execution worker without restart; without them
paper uses synthetic demo candles and operator `runtime` reports `DEMO_MARKET_DATA`). Sub-hour
live pauses unless the authenticated user-order feed is connected; a pause caused only by the feed
clears automatically once it is healthy. A definitively rejected live order is recorded
`rejected` and the book continues; an ambiguous submit stays `unknown`, is looked up at Coinbase
by client order id, and is never re-submitted ([ADR 0078](../decisions/0078-live-readiness-http-ack-venue-reload-definite-rejects.md)). Live start, live resume, and
live Trade-page orders each ask for an explicit live confirmation first. Paper deploy and
new paper tickets accept optional maker/taker **assumptions** (UI Deploy/Trade, or
`thytrader-runtime --maker-fee-rate` / `--taker-fee-rate`). Omitted paper rates stay `0.001` /
`0.002`. They are documented fill costs, not observed Coinbase fees. Live rejects those fields and
keeps venue-recorded fees.

Higher-timeframe filters and separate indicator clocks load enough warmup for both the first
decision's current and previous completed bars. Starting a 1h bot just after a 4h boundary can
therefore need 51 fetched 4h candles for a declared 50-bar warmup. Missing required candles still
pause the bot. A complete archived dataset does not prove its execution window is healthy;
inspect the bot's Decisions and reconciliation reports. A repaired data mismatch needs an explicit
resume (including live acknowledgement for live bots), followed by a fresh decision check.

Saving or backtesting a strategy is not deploying it. Deploy, pause, resume, and stop are explicit — on
the Run stage or through `thytrader-runtime` with the gates in [Safety](safety.md). Default stop is
**managed shutdown**: protective brackets stay and residual exposure stays in account-level risk
until the book is flat. Pass `--flatten` only when the operator asked to marketably exit then cancel
remainders. A flatten cancels protective orders first (waiting on Coinbase's asynchronous cancel
without pausing), never rests a new bracket, and ends `stopped` / `flat`. A live bracket or exit
that Coinbase rejects pauses once (`PROTECTIVE_SUBMIT_REJECTED: …`) and identical retries back off
instead of repeating every poll. Pause still maintains attached-child protection; it does not reset daily-loss or
drawdown baselines. Live books size from allocated capital or venue available quote, not ledger
`cash`. `GET /api/v1/deployments` and `thytrader-runtime show` expose a `capital` block
(`allocated_capital`, `venue_available_quote`, `reserved_buying_power`, `inventory_cost`,
`performance_equity`, and durable breaker baselines) separate from top-level `cash`
([ADR 0065](../decisions/0065-deployment-capital-accounting-http.md)). Operator `runtime` shows
`lifecycle_command`, latches, `revision`, and whether a worker lease is held without cash.

A multi-instrument document still starts **one** deployment. Deploy and
`GET /api/v1/deployments` list every product book (`positions`, `instrument_runtimes`) with
protection status. Orders and fills carry `product_id`. `book_totals` must match those
collections. The singular `position` field is compatibility-only (the focused book, always
product-tagged); do not treat it as the full inventory
([ADR 0060](../decisions/0060-multi-book-deployment-api.md)). Operator `strategies` / `runtime`
reports include redacted `books[]` (product, phase, side, protection coverage quantities only —
no prices, cash, or order payloads).

Bot detail, Portfolio rows, Home, and portfolio sleeves describe a book by its **position state**,
not its raw phase ([ADR 0097](../decisions/0097-runtime-parity-and-observability.md)). Right after
an entry fills, the TP/SL bracket (or the stop-only order) rests and the worker's `phase` reads
`pending_exit`, but the book shows **Open · protected (TP/SL resting)** (or **(stop resting)** with no
take-profit). **Exiting** appears only while an exit is actually being sent: a marketable exit, a
matched exit rule, or a flatten. HTTP and operator payloads carry the same reading as
`position_state` and `exit_in_flight`. An open paper book's `protection_status` is always
`covered`, on list and summary reads too: its stop is enforced on every closed bar and any
take-profit rests in the paper broker
([ADR 0098](../decisions/0098-library-views-book-marks-portfolio-fills.md)).

Protection badges distinguish **Worker stop** (paper), **Order state fresh** (live, amber), and
**Unverified** (missing/stale evidence). Fresh live coverage requires enough identified OPEN
stop quantity, matching executable kind and persisted stop/target geometry, with each contributing
order's actual `venue_observed_at` receipt within 120 seconds. Local bookkeeping timestamps never
refresh it; UNKNOWN/error reads clear it and legacy rows stay unknown until actually read.
`protection.observed_at` is the latest relevant receipt; `verified_at` is the oldest contributing
fresh receipt (only the covered fraction when coverage is partial). The badge's submitted
`geometry_basis` is not an independent venue geometry audit, whole-account reconciliation, or
fill guarantee. Even fresh order state is amber, explicitly **venue geometry not independently
verified**. Profitable trailing stops may cross entry; partial or TP-only orders cannot provide
full stop cover. These are read-only reporting rules, not an automatic pause or replacement order.
See [ADR 0112](../decisions/0112-quantitative-protection-evidence.md) and
[ADR 0119](../decisions/0119-venue-order-observation-provenance.md).

Each open book also shows its **unrealized PnL** at the close of the last bar the bot evaluated,
and how long it has been held. On bot detail these are the **Unrealized** and **Held** columns of
Positions & protection, plus a uPnL line on the Position card. On Portfolio sleeve rows each open
book shows a state chip (Protected, Unprotected, Unverified, or Exiting), its uPnL, time held, and
entry / SL / TP. The figure labeled **net** subtracts recorded entry fees allocated to the
quantity still held, including after partial exits or added entries. Future exit fees are excluded;
the tooltip states the fee basis and the bar close used. When entry-fee evidence is unavailable,
the figure is explicitly labeled **gross** (before entry and exit fees). Without an evaluated bar
it shows `—`, never an estimate.

Bot detail's aggregate ledger uses those same per-product bar closes for PnL and exposure.
If any open book lacks a journaled close, the aggregate stays unknown even when another book
has a mark. Operator runtime checks use the same rule; the separate Performance report uses
market-data closes and may have a price when the decision journal does not.

When one bar makes several exits due, paper takes the same one the backtest does: the stop first,
then a take-profit the bar touched, then the exit rule, then the time exit. A bar that trades
through the stop exits as a stop even if the time exit is also due on that bar.

To see why a live bot entered quickly while its paper twin waited, read
`uv run thytrader-operator portfolios`: `paper_live_fill_comparisons` pairs paper and live bots
explicitly linked with identical pinned trading rules and reports entries rested, filled, and expired, the fill
against the limit, and the time to fill. A paper post-only entry fills only when a closed candle
trades through the limit. Live fills whenever Coinbase matches it. The Portfolio page shows the
same comparison for a portfolio's own sleeves (see **Paper vs live** below).

Use **Paper/live twin** on Bot detail to select a matching opposite-mode strategy bot, then
confirm **Link twins**. **Unlink twin…** confirms removing that comparison pairing. The CLI
provides the same controls:

```bash
uv run thytrader-runtime show-twin BOT_ID
uv run thytrader-runtime link-twin BOT_ID --counterpart-deployment-id OTHER_BOT_ID --confirm
uv run thytrader-runtime unlink-twin BOT_ID --counterpart-deployment-id OTHER_BOT_ID --confirm
```

Both bots must share the exact rules snapshot, primary market, and timeframe. Each bot has one
partner; unlink before replacing it. These controls only choose comparison partners. They never
start, stop, arm, or trade either bot. Confirmation is required even with YOLO; no live
acknowledgement is needed. Existing bots stay unlinked until selected. Unlinked bots have no
comparison, and several pairs can share rules. After an uncertain write, refresh/read the link
before retrying ([ADR 0102](../decisions/0102-explicit-paper-live-twin-links.md)).

On-demand trades and strategies use `entry.side` of `long` or `short`. CLI `--side`
defaults to `long`. A short is a Coinbase **spot** sell-to-open: live fails closed without
available base and never borrows. When stop and take-profit are known and trailing is off, live
attaches those exits to the entry; paper still uses synthetic exits. A strategy whose **Take
profit** is set to **None** (Build → Exit conditions) exits only on its stop, ATR trail, or time
exit: paper rests no take-profit, and live protects the position with one Coinbase stop-limit at
the stop (limit 5% through it, like a bracket's stop leg). **Exit when** (Build → Exit
conditions, optional) adds a rule built like the entry conditions, for example *fast crosses
below slow* to hold a trend until it reverses. It is checked on every closed bar after the entry
filled; when it matches, the bot sells at that bar's close as a taker, like the time exit. The
initial stop keeps guarding the position until then and wins if the same bar hits it, and the
trailing stop, take-profit, and time exit still apply. Live cancels the protective order before it
sells. The bot timeline shows such exits as **signal exit** with the rule that matched, and the
**EMA trend hold** template starts from this shape. The Build stage also lists advisory
warnings under the saved definition, such as a short whose take-profit can fall to zero at normal
volatility; they never block saving or running. Command examples live in
[`skills/thytrader-runtime/SKILL.md`](../../skills/thytrader-runtime/SKILL.md).

**Reference instruments** (Build → Market and data, optional) let a strategy watch up to three
other markets without trading them, for example "trade this alt only while BTC-USDC's daily close
is above its EMA(100)". Add a reference (product in the same quote currency, on the strategy's clock
or a coarser one), then choose it as an indicator's **Instrument** under Indicators. Rules show such
operands as **BTC · EMA(100) @ 1d**, and the summary states the gate. Only reference bars that have
already closed count. Paper and live bots need each reference series on the data watchlist before
they start (the refusal names the `thytrader-data watch-add` command). While a reference is late or
missing they skip new entries (timeline: **reference data stale** / **reference data missing**) and
keep managing open positions. Orders always go to the strategy's own market. The **BTC regime
gate** template starts from this shape.

### Portfolio and bot detail

**Portfolios** ([ADR 0088](../decisions/0088-portfolio-model-and-portfolio-backtest.md),
[ADR 0091](../decisions/0091-portfolio-deployment-limits-and-manager-proposals.md)) sit at the top of **Portfolio**
(http://127.0.0.1:5175/deployments). A portfolio is a set of sleeves — one strategy each, with a
capital weight — plus a cash reserve, shared limits, and manager settings. It is **Paper** or
amber **LIVE**, never both; mode and quote currency are fixed when you create it. The header has
**New portfolio…** (name, mode, quote currency, capital, cash reserve %), and a row below it has
one button per portfolio (with its mode chip; long names are shortened, the full name is the
tooltip). The card shows the deployment state (Not deployed, Running, Partly running, Paused,
Stopped), capital, allocated share, cash reserve, sleeve count, and, once started, the run's equity
with its profit or loss and the exposure. **Start portfolio…** starts one bot per sleeve (paper:
weight × capital of paper cash; live: weight × capital of allocated capital) after a confirmation
that lists each sleeve's capital; a live portfolio also needs the real-orders checkbox. If any
sleeve is refused (its strategy already runs elsewhere, or the risk policy says no), nothing starts
and each reason is listed. **Pause all**, **Resume…** (live: checkbox again), and **Stop…** (managed
stop or stop and flatten) act on every sleeve. Four tabs (kept in the URL as `?portfolio=&tab=`):

- **Sleeves** — each sleeve's strategy, weight, market and clock, capital slice, its bot (status
  linked to the bot's page, with its profit or loss; "Paused by breaker" when a portfolio breaker
  holds it), any other bot of the strategy outside this portfolio, and issues (invalid rules or a
  changed quote currency). Each row has **Start…**, **Pause**, **Resume…**, or **Stop…** for that
  sleeve alone; a sleeve with a running bot cannot be removed until it is stopped. **+ Add sleeve from a strategy** opens a searchable picker (other-quote
  strategies and existing sleeves are disabled). **Edit weights** edits every weight and the cash
  reserve at once and refuses more than 100%. The aside shows allocation bars, the largest single
  asset against the per-asset limit, and the cash reserve. When a sleeve's bot has a paper or live
  saved twin (a deliberately linked opposite-mode bot with identical pinned trading rules), the row links to a **Paper vs live** panel
  below the table. For each twin it shows a paper row and a live row with the share of entries
  that filled, the average fill against the posted limit in bps (positive is worse), the median
  wait to fill, and how much sooner one side fills
  ([ADR 0098](../decisions/0098-library-views-book-marks-portfolio-fills.md)).
- **Portfolio backtest** — fee rates prefill from the fee-tier suggestion; **Run portfolio
  backtest** queues a job and shows its progress, then the combined equity against an
  equal-weight buy-and-hold basket, net return, max drawdown, best sleeve alone, idle capital,
  Sharpe, what each sleeve contributed, correlations, overlap, the disclosures (sleeves are
  simulated independently; portfolio caps and cross-sleeve interactions are not simulated), and
  earlier runs. A rejection lists the problem per sleeve (missing datasets, invalid rules, no
  common window).
- **Manager** — the manager agent's **proposals** (rebalance, pause or resume a sleeve, add a
  sleeve), each with its reasons, the evidence it cites, and why it waits for you, with
  **Decline**, **Ask why** (opens the Agent panel with the proposal as context and a drafted
  question; nothing is sent until you press Send), and **Approve…** (approving a live resume needs
  the real-orders checkbox). Below: the editable mandate and permissions — rebalance within a
  weekly budget (applied on its own on paper; on a live portfolio every rebalance waits for you),
  pause a sleeve (applied on its own when allowed), propose sleeves (each waits for you); it never
  places orders — and the append-only journal of every change, with who made it. The manager agent
  runs outside ThyTrader (Hermes or Claude through the portfolio skill).

  The agent briefing marks open books at their latest journaled close and includes verified
  entry fees and net unrealized PnL, even when recent decisions are omitted. Unknown marks or
  fee evidence remain null. Portfolio exposure counts inventory cost and working entry
  remainders; verified exit orders, including paper take-profits, do not add entry exposure.
- **Limits** — max total exposure, max per asset, optional daily loss and drawdown stops, and the
  **breakers**: equity this run, today's change against the daily loss stop, drawdown from the
  run's peak against the drawdown stop, and exposure per asset against the caps. On a deployed
  portfolio every sleeve's new entries must fit the caps (a refused entry shows the portfolio
  reason on the bot's decision timeline). A tripped stop pauses every sleeve and stays latched:
  **Reset breaker…** clears it and re-baselines; sleeves stay paused until you resume them.

Every change is revision-guarded: if someone else changed the portfolio first, the page reloads it
and says so. Deleting a strategy removes its sleeves (journaled). Agents use
[`skills/thytrader-portfolio/SKILL.md`](../../skills/thytrader-portfolio/SKILL.md) (composition and
the manager loop) and `thytrader-runtime portfolio-*`
([`skills/thytrader-runtime/SKILL.md`](../../skills/thytrader-runtime/SKILL.md)) to start and stop.

#### Create a portfolio from a file

Agents can save a portfolio and its initial sleeves in one call at revision 1
([ADR 0101](../decisions/0101-atomic-portfolio-creation-with-sleeves.md)). Save this as
`portfolio.json`, replacing the example ids with existing strategy ids:

```json
{
  "name": "Core",
  "mode": "paper",
  "capital_quote": "1000",
  "cash_reserve_fraction": "0.2",
  "sleeves": [
    {"strategy_id": "01a0f000-0000-7000-8000-000000000101", "weight_fraction": "0.5", "note": "BTC"},
    {"strategy_id": "01a0f000-0000-7000-8000-000000000102", "weight_fraction": "0.3"}
  ]
}
```

Run `uv run thytrader-portfolio create --file portfolio.json --confirm` from the repository root.
The file also accepts `quote_currency` (default USDC), `limits`, and `manager`; CLI flags override
those settings. Omit `sleeves` or use `[]` to create an empty portfolio. At most 32 distinct
strategies are allowed, all with readable markets in the portfolio's quote currency; weights plus
reserve must be at most 1. If any sleeve is refused, nothing is created. The journal records
creation and each sleeve at revision 1. Creation only saves a definition, even for `mode: "live"`;
starting trading remains a separate, gated `thytrader-runtime portfolio-start` action.

**All bots** (below the portfolios) lists every bot: one row per deployment, grouped
**Needs attention** (any status other than running, paused, or stopped), **Running**, **Paused**,
and **Stopped**. An **All / Paper / Live** switch filters the rows. Each row shows the strategy name
(from the deployment's captured `strategy_name`; a live bot whose strategy was deleted reads
"<name> (deleted strategy)"; or "Discretionary order"), a **Paper** or amber **LIVE** chip, the market with the
product's own quote (`ETH / USDC`) and clock, position and protection, fill-ledger net PnL (`—` when
not computed), and status. Rows open `/deployments/{id}`. The list is paged 50 at a time; the filter
applies to the page you are on. The header counts running, paused, and needs-attention bots across
the whole inventory, and totals allocated capital, performance equity, and gross marked exposure
only from the deployments' `capital` and ledger fields, per quote currency. Money is never totalled
across paper and live (choose Paper or Live), and a figure that cannot be computed truthfully (no
capital block, an open position without a complete mark) shows `—` with the reason. **Start a
deployment** opens the strategy library; start from a strategy's Run stage. Until portfolio
deployment ships, each bot runs on its own capital.

**Bot detail** (`/deployments/{id}`) is anchored to the snapshot the bot started with.
The header shows the strategy name (or "<name> (deleted strategy)" for a kept live bot), the mode
chip, a link to the strategy workspace, a **Current rules** / **Earlier edit** marker ("This bot is
running an earlier edit" offers **What changed** and **Update bot…**: managed stop, then a new start
on the current rules, each confirmed), market · clock · status · worker lease (a held lease is coordination
state, not proof the worker is healthy), instruction, entry eligibility, and revision, plus
**Pause entries…**, **Resume entries…**, **Stop…** (managed stop or stop and flatten), and
**Flatten remaining exposure…** when a managed stop left residual exposure. A stopped bot never
offers Resume. Four cards show allocated capital and performance equity (the `capital` block; live
adds venue availability), PnL (operator performance report, with currency, drawdown, and incomplete
mark caveats; ledger fallback when the report is unavailable), position and protection, and the
latest completed-bar signal; the header also shows **Next evaluation ≈** the close of the bar after
the last evaluated one. **Orders & fills** is one card with an Orders / Fills switch; each list
keeps its own cursor paging. **Decisions** is the bot's per-bar timeline
([ADR 0087](../decisions/0087-per-bar-decision-timeline.md)), newest bar first, with an
**All / Trades / Blocked / No signal** filter. Each row shows the bar time, an outcome chip (Entry,
No signal, Holding, Exit, Blocked, Skipped, Error), and a one-line reason such as
"No trade: RSI(14) 47.21 needs ≥ 50". Expanding a row shows each entry condition as a chip with
its actual value versus the threshold (ALL / ANY / NOT grouping and the HTF filter when the
strategy has one; an indicator on another clock reads like "RSI(14) [4h]" and one with a bar
offset like "Highest(3, high) (1 bar ago)", showing the lagged value), the risk verdict, linked orders and fills, the position at the end of the bar,
and the bar's trade reason when it persisted an order intent (trade reasons are merged into their
bar, not listed twice; reasons with no journaled bar, such as those recorded before the journal
existed, stay below as **Earlier trade reasons**). **Load more** pages older bars. The journal keeps the newest 20,000 decisions per
bot for up to 180 days; "Decision history is unavailable" means the API has no database. A latched breaker shows
a banner with **Reset breaker latches…** (its own confirmation). Positions & protection, evidence
links (**Backtests of this strategy** and **Decisions** open the Test and Why stages), other
deployments of the same strategy, and the **Capital breakdown** and **Exact configuration** (the
bot's snapshot) disclosures follow. Live resume, here and on the Run stage,
keeps its confirm disabled until you tick "I understand this places real orders on Coinbase with
real money"; only then is `i_understand_live: true` sent. Incomplete or malformed lifecycle payloads
(including an unknown `lifecycle_command`) stay read-only, and an ambiguous or stale mutation
disables controls until a refresh succeeds. Discretionary deployments have no strategy source.
Start a new deployment on the Run stage; manage an existing one there or on its detail page.

### Trade

**Trade** (http://127.0.0.1:5175/trade) places a one-off long or short with a stop loss and take
profit through the same intent → risk → broker path as `thytrader-runtime place-order --confirm`.
The form is on the left: a **Paper / Live** switch, product, side, order type (post-only limit or
marketable), clock, limit price, quantity or quote notional (exactly one), stop loss, take profit,
paper cash and fee assumptions (paper only), and an optional why note. The **Review** aside on the
right shows the entry, max loss at stop and reward : risk computed exactly from your numbers (before
fees; **Unknown** for a marketable entry), whether a risk policy is published (read from
`GET /api/v1/risk-policy`; the server still checks every order), and warnings when the stop or
target sits on the wrong side of the entry. **Live** turns on the live chrome and an amber aside;
**Review live order…** opens a live confirmation whose **Send** stays disabled until you tick "I
understand this places real orders on Coinbase with real money". ThyTrader never borrows: live
shorts need available base. A timeout is reconciled by client order id, never re-sent. Recent
why-trade records stay below the ticket.

### Why

Protective bracket or take-profit replacements keep a held book **Holding**. Their canceled and
replacement orders remain linked in the bar; they are not canceled entries. Historical rows
retain the worker's original explanation.

For blocked entries, exposure details show existing exposure, the proposed amount, account
capital, and the cap. Live account capital uses one observed quote balance plus managed long
inventory cost and quote reserved by buy entries. Each bot's allocation and each portfolio's
limits still bind separately. A `BREAKER_MARK_MISSING` detail identifies the bot and distinguishes
missing inventory prices from missing equity baselines. Diagnose before changing a limit; a
healthy bot can still have a correctly refused entry.

A strategy's **Why** stage (`/strategies/{strategy_id}/why`) shows the same per-bar Decisions
timeline across every deployment of the strategy, newest bar first, with a deployment selector
(**All bots** or one bot, each marked Current rules or Earlier edit) and the same filters and
expandable rows as bot detail. Every completed bar a paper or live bot processed has a row that
says what it decided and why: the entry rule's values versus thresholds when it was evaluated, a
skip reason (cooldown, max open positions, warming up, entry order working, paused, stopped, data
gap, user-order feed down, catch-up after downtime) when it was not, the risk verdict when an entry
was blocked, and the exit reason (stop, trail, target, time, flatten) when a position closed.
Persisted trade reasons (risk decision, reconciled order and fills, notes) appear inside the bar
that created their intent. The UI must not
silently relabel a `BASE-USD` book’s PnL as USDC. Performance quote comes from the snapshot's
instrument (or discretionary product), with unknown provenance shown explicitly.
Lifecycle controls require the full deployment contract (`lifecycle_command`, both breaker
latches, `revision`, and `worker_lease_held`); incomplete or malformed payloads stay read-only.
Managed stop is the default and is not a flatten. Flatten requires an explicit choice and may
settle asynchronously; pause still maintains protective exits.

### Journals

Open http://127.0.0.1:5175/journals for origin-attributed facts, lessons, and notes already stored
by experiential memory. Why-trade review is on [Memory](http://127.0.0.1:5175/memory) and
[Trade](http://127.0.0.1:5175/trade). Append still uses `thytrader-memory add-journal --confirm`.

### Operator chat

Click **Agent** in the top bar on any page, or open http://127.0.0.1:5175/chat for the full-page
view. Both are the same chat with the same gates. Paste **your LLM API key** (OpenAI or OpenAI-compatible). This is
not a Coinbase form — Coinbase keys stay on the separate secrets surface and never enter the
browser. The key is held in the API process only; restarting the API clears it.

The chat is an operator over the same gated skill lanes as `ops/`. Read-only diagnosis runs
immediately. Data, research, runtime, and memory mutations wait for in-app confirmation. Live start,
live resume, and live place-order also need the understand-live checkbox. Paper start and paper
on-demand orders may include maker and taker fee assumptions; live Coinbase fees stay venue-recorded.
`uv run thytrader-operator chat-status` reports whether a key is configured; it never prints the
secret.

### Settings

Open http://127.0.0.1:5175/settings. YAML is the source of truth for non-secret knobs: YOLO on/off,
independent tiers (`data`, `research`, `paper`, `live`), log level, intervals, lookback, default
ingest product, and notify provider. Changes apply without restarting API or workers. Secrets stay
in ignored `.env`. Leftover `THYTRADER_YOLO_TIERS=paper` is valid. Live still needs
`--i-understand-live`. The Coinbase section beside that panel sets, rotates, or clears Advanced
Trade API secrets. Keys stay server-side. The form wipes before the request; GET never echoes them.
LLM keys stay in the Agent panel / `/chat`.

## With an agent (or the CLIs yourself)

Run every `uv run thytrader-*` command from the **repository root**. JSON is the default CLI
output. HTTP talks to the loopback API unless you pass `--local` on purpose. The CLIs resolve its
base URL from `--base-url`, then `THYTRADER_API_BASE_URL`, then the `THYTRADER_API_HOST` /
`THYTRADER_API_PORT` settings (default `127.0.0.1:8200`; installs may override the port). Use
`"$THYTRADER_API_BASE_URL"` for raw `curl`. When a command fails it says what failed (HTTP status and
API error code, timeout, unreachable origin, or a dropped connection) and what to do next.

Backtests, studies, and portfolio backtests run in the `research-worker` service that `make run`
starts, never in the API, so heavy research does not slow the UI or other commands
([ADR 0092](../decisions/0092-research-worker-pool.md)). It runs
`THYTRADER_RESEARCH_WORKER_COUNT` jobs at once (default 2, memory-safe on an 8 GB machine); more
wait as `queued`. A synchronous run that takes longer than about 25 s comes back as its queued or
running job (the UI keeps polling it for you; the CLI prints `next_action`). `uv run
thytrader-operator health` shows whether the research workers are live, each worker's memory
(`rss_bytes`), and how many jobs are queued or running and for how long the oldest has waited.

```bash
uv run thytrader-operator health
uv run thytrader-research list-strategies
uv run thytrader-portfolio list
uv run thytrader-portfolio backtest --portfolio-id UUID --maker-fee-rate 0.004 --taker-fee-rate 0.006 --fixed-slippage-bps 5 --wait --confirm
uv run thytrader-research create-strategy --confirm
uv run thytrader-research create-strategy --template rsi-mean-reversion --confirm
uv run thytrader-research save-strategy --strategy-id UUID --file document.json --revision 1 --confirm
uv run thytrader-research bulk-delete-strategies --strategy-id UUID --dry-run
uv run thytrader-research plan-study --file study.json
uv run thytrader-research submit-study --file study.json --confirm
uv run thytrader-research list-studies
uv run thytrader-playbook status
uv run thytrader-memory status
uv run thytrader-runtime decisions DEPLOYMENT_UUID --outcome entry_blocked
uv run thytrader-operator decisions --strategy-id STRATEGY_UUID
uv run thytrader-runtime show-settings
uv run thytrader-runtime set-settings --yolo-enabled true --yolo-tiers paper --confirm
```

`uv run thytrader-operator decisions --deployment-id UUID` reads the same recorded conditions,
indicator values, and UTC signal timestamps as the bot timeline. A schema-validation failure
means the diagnostic could not be read; it does not mean the bot has no decisions or no signal.

`thytrader-operator strategies` includes only the 100 most recently updated library rows;
`partial_result_warnings` reports truncation. An older bot's strategy may still exist even when it
is absent from those rows. Use `thytrader-research show-strategy --strategy-id UUID` for its current
rules, or `list-strategies --limit 100` followed by the returned `--cursor` to read the full library.

| Lane | What it may do | Gate |
|---|---|---|
| `thytrader-operator` | Read-only diagnostics | none (never trades) |
| `thytrader-data` | Watchlist, ingest, gap-fill | `--confirm` on mutations; writes send installation Bearer when a token is resolvable |
| `thytrader-research` | Strategy create/save/import/clone/delete, backtests, composed studies, study catalog | `--confirm` on mutations; cannot deploy or trade |
| `thytrader-runtime` | Paper/live start, pause, resume, stop, on-demand place-order, risk policy, YAML settings, write-only Coinbase credentials; read-only `list`, `show`, and per-bar `decisions` | `--confirm` on mutations; live also `--i-understand-live`; `set-settings` and credential set/clear never YOLO |
| `thytrader-playbook` | Sequence data → research → optional paper | forwards `--confirm`; **never live** |
| `thytrader-memory` | Journals, why-trade review, sentiment/pattern hooks, monitor, notify, fail-closed train | `--confirm`; YOLO never covers this lane |

Ingest is a worker job (HTTP 202). The API dataset volume stays read-only. Prices are never
interpolated. Coinbase returns no candle for an interval without trades, so a thin market's quiet
bars are published as flat no-trade bars at the previous close with zero volume
([ADR 0095](../decisions/0095-sparse-markets-no-trade-bars-listing-floors.md)). One ingest queue keeps walking until `watch_complete` or a
durable failure. If the market listed after the lookback start, the worker proves the listing and
records it as `history_floor_at`, and reports the watch as complete from that floor. Backtests can
use the covered range. Earlier bars are never invented, and results over quiet bars disclose
`synthetic_no_trade_bars`. To check that a series is healthy, read its `data-catalog` row:
`watch_complete: true` and `watch_covered_candle_count` equal to `watch_expected_candle_count`
(unless a listing floor is set). The Home data-health table shows the same coverage as "X / Y",
adds "· N no-trade" when no-trade bars exist, and shows "Complete from listing" when a floor is
set.

Venue order observations are separate from local bookkeeping timestamps. A restart or migration
does not assert that an order was freshly checked; legacy observation times stay unknown until
reconciliation reads the order. This is not a guarantee that a stop-limit will fill or a full
account audit ([ADR 0119](../decisions/0119-venue-order-observation-provenance.md)).

For **freshness across all enabled watches**, run `uv run thytrader-operator data-health`, or
open Home → Data health → Watched-market freshness. This read-only snapshot compares each
published tail with its own latest closed candle and reports how many closed bars are missing.
Daily and 6h markets are not judged by a 1-minute clock. A successful ingest chunk and a
complete historical island do not prove a fresh tail; historical watch coverage remains a
separate field. `settling` means only the newest close is inside the 120-second publication
grace. `stale`, `missing`, or `invalid` needs inspection; an incomplete inventory is not an
all-clear. Refresh explicitly to obtain a new snapshot. This is published-dataset health, not
proof that an individual bot has evaluated or reconciled its latest bar.

`inspect-gaps` may return `truncated` with a partial `gap_summary` when a server-side budget
stops the scan ([ADR 0072](../decisions/0072-catalog-health-bounded-gaps-self-complete-ingest.md)).

Command details live in the canonical skills under [`skills/`](../../skills/README.md). Do not
scrape logs, query PostgreSQL, or print `.env`.

For a numbered **portfolio visibility → data health → research** path (account balances, deployment
inventory, fingerprint copy, backtests), see
[`docs/agent/portfolio-research-ops-playbook.md`](../agent/portfolio-research-ops-playbook.md).

### Read-only signal evaluation

The entry rule of any backtest result can be traced bar by bar through the API, which owns the
verified datasets:

```bash
uv run thytrader-research-evaluate <result_fingerprint> --outcome matched --pretty
```

A `run_fingerprint` of a completed backtest also works. The command prints one bounded page of
the completed-candle entry-condition trace (each bar's indicator values and `matched` /
`not_matched` / `undefined`), outcome counts, and a `next_cursor` for `--cursor`. The API
re-evaluates the exact run and refuses to answer unless the trace reproduces the result's
recorded trace fingerprint. It does not publish a run, create an order intent, apply cooldown,
simulate entries or exits, calculate PnL, persist results, or mutate trading state. Paper and live deployment is a
separate runtime (the workspace Run stage or `thytrader-runtime`), not this CLI.

The Mine / Research / All badges count all matching strategies, including rows beyond the
visible page, and respect the selected tag. Async study acceptance pins inputs; poll the job
for planning failures or run `plan-study` first. A bot's newest decision candle may settle for
up to two minutes after its UTC close: the activity reads "newest candle settling", entries
wait, and protection/reconciliation continue. An expired wait or older gap still pauses the bot.

Explicit twins may be strategy clones with server-verified identical pinned trading rules
(ADR 0105). Each comparison side names its actual snapshot fingerprint; pairing changes only
comparison metadata, never bot lifecycle or trading rules.

Account GETs retry once after 0.5 seconds only for timeout/network or HTTP 502/503/504.
The repeated request is freshly signed on the same pagination cursor; exhausted failures
return no partial balances. Authentication, 429 rate limits, malformed responses and
pagination errors do not retry. Failed-read evidence includes `attempts` (1 or 2).
Order submissions and cancellations never use this retry helper.

Research reliability: [frozen campaigns, prospective validation, economic preflight,
stress assumptions, and bounded exports](research.md) are shipped through the research
HTTP/CLI lane and `/research` UI. They confer no deployment or order authority.

After an update, `make run` applies the migration head and rebuilds every service.
Health compares the applied database revision with the shipped expected revision
(currently `0066`, including durable safety alerts). If a mismatch persists after rebuilding latest main, report it;
do not bypass the readiness check.
