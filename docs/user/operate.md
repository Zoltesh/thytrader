# Operate ThyTrader

You can drive ThyTrader from the browser, from confirmation-gated CLIs, or both. An authorized
agent can do the same loop **100%** through the shipped skills. Nothing requires an agent.

When an agent is operating a **running** instance, open [`ops/`](../../ops/README.md) rather than
the git root. Skills of record: [`skills/README.md`](../../skills/README.md).

## In the browser

After [setup](setup.md), open http://127.0.0.1:5175.

The first usable slice shows deterministic demo balances when Coinbase credentials are empty, and
live balances when both Coinbase variables are configured. That screen never submits an order.

### Finding your way around

The left rail has four destinations ([ADR 0079](../decisions/0079-four-destination-shell-agent-panel-palette-tokens.md)):

| Rail item | Opens | Also holds |
| --- | --- | --- |
| **Home** | `/`: portfolio overview | |
| **Strategies** | `/strategies`: library | Each strategy's workspace: Build `/strategies/{id}`, Test `/test`, Run `/run`, Why `/why`. Old `/research`, `/backtests`, `/deploy` links redirect there |
| **Portfolio** | `/deployments`: running and past deployments | `/deployments/{id}` detail |
| **Trade** | `/trade`: on-demand order ticket | |

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

### Strategies

Open http://127.0.0.1:5175/strategies when the stack is healthy. The library requests one
server page at a time (10 rows by default; select 10, 25, 50, or 100). Use Next/Previous to
navigate cursor pages. Changing the page size returns to page one. A failed page shows a retryable
error rather than an empty library. Other selection screens may still load the full library.
Each row shows the strategy name and short fingerprint, market (`BTC / USDC`) and clock, the
latest version (an open draft over published history reads `v3 · draft v4`), a progress pipeline,
the latest backtest, and when it was updated. The pipeline chips are evidence, not readiness:
**Build** (draft open or published), **Test** (a backtest exists), **Paper** and **Live** (newest
deployment status per mode: running, paused, stopped, or not deployed). Clicking a row opens that
strategy's workspace; the latest-backtest link opens that result on its Test stage.

From the library you can create the conservative reference draft, clone a published strategy into a
fresh draft identity (Clone stays ungated), import a complete strategy definition JSON as a new
draft, and archive an immutable publication after confirming the latest published version and
fingerprint in the Archive dialog (Cancel is the default focus; Escape cancels). Browser writes first establish a CSRF session and send its matching token and cookie;
the app handles this automatically. A 401 CSRF error is a browser-session/client failure, not a
strategy-validation error. Do not disable the trust boundary to work around it.

### Strategy workspace

Every strategy has one workspace ([ADR 0080](../decisions/0080-per-strategy-workspace-build-test-run-why.md)).
A sticky identity bar shows the name, **Draft vN** or **Published vN**, a version picker
(published versions plus the open draft), the short fingerprint with **Copy full fingerprint**,
the market with its Coinbase product id, the clock, and the draft state (unsaved changes, or
**No editable draft** with **Revise into new draft**). **Versions** opens the history (export,
semantic diff, Edit into next draft); **Clone** copies the selected published version into a new
strategy. The stage links are **Build · Test · Run · Why**.

`?version=<strategy_fingerprint>` pins the exact published version for Test, Run, and Why; without
it the latest published version is used. A fingerprint that does not belong to the strategy shows
an error and no stage content, so nothing can be tested or started against a different version.

**Build** (`/strategies/{strategy_id}`) reads that identity's version history directly and opens its
durable draft without loading the full library. Rules read as IF / AND / OR rows in a nested
ALL/ANY/NOT tree; the right column shows **In plain English**, **Checks** (validation, warmup and
required data, collapsible engine support), save state, **Save draft**, and **Publish vN…**. Publish
asks for confirmation ("Publish immutable strategy version?") and never starts trading; the success
panel offers View published version and Set up a backtest. If the identity has only immutable
published or archived versions, Build says **No editable draft** and shows the published
definition read-only in the same layout. That state is not a missing strategy or an API outage.
Saves carry an opaque revision and reject stale browser tabs rather than overwriting newer edits;
leaving Build with unsaved edits asks first.

### Test (research and backtests)

Open a strategy's **Test** stage (`/strategies/{strategy_id}/test`; old `/research?strategy=` links
redirect here, and `/research` alone points you to the library). The run bar uses the workspace's
selected immutable version and shows the verified dataset, evaluation period, initial capital,
maker/taker fees, fixed slippage, engine, and the V2 constant-spread stress assumption. Omitting
both evaluation dates on submit uses the common LTF+HTF (and extra-clock) covered intersection
rather than the LTF range alone. When Coinbase credentials are present, maker/taker fields prefill
from fee-tier suggested defaults and stay editable; demo or missing credentials leave those fields
blank rather than inventing a tier. It lists every stored result for each exact published version
and compares the latest result across versions; dataset and per-version result failures remain
visible without hiding strategy evidence. Result summaries report the published strategy clock,
including `2h` and `4h`, not a hardcoded `1h`. **Run a study** opens the composed-study builder
(OOS holdout, walk-forward, parameter sweep, walk-forward optimization) for the same version. Below
the run bar, **Results for this strategy** lists every published result for every version; opening
one shows it inline (`?result=` deep link) with its assumptions. Results are research evidence, not
a promise, and there is no Deploy or Start-paper button on them.

**Validate & publish immutable version** (`POST /api/v1/strategies/{strategy_id}/publish`) atomically
consumes that mutable draft and records canonical strategy evidence; it does **not** start paper or
live trading. A published version can be archived from the library after confirmation: that appends
a permanent archive marker and hides it from active selection without changing its fingerprint or
canonical bytes. Backtests require a verified dataset fingerprint and remain deterministic research
artifacts.

### Backtests

Open `/backtests` to inspect immutable result summaries across all strategies. An old
`/backtests?result=` or `/backtests?strategy_fingerprint=` link opens the owning strategy's Test
stage when that fingerprint is one of its published versions; otherwise the standalone view stays. The list requests 10 newest-first rows
by default; its Rows per page selector offers 10, 25, 50, and 100. Newer/Older request only the
current server page, and changing the size restarts at the newest results. A full page does not
by itself imply there are older results: the server's `has_more` indicates that. Opening an
individual result does not require loading every list page.

### Paper and live

Open a strategy's **Run** stage (`/strategies/{strategy_id}/run`; old `/deploy?strategy=` links
redirect here). A **Paper** card and a **Live** card sit side by side, each listing deployments of
the selected exact version with status, instruction, entry eligibility, fill-ledger performance,
exposure and protection, and an **Open bot →** link to `/deployments/{id}`. Other versions'
deployments are listed separately. **Start paper deployment…** asks for confirmation and starts a
new deployment of the version (never a promotion of a backtest). Pause, resume, stop, and flatten
use the lifecycle dialog. **Arm live trading…** opens a dialog whose confirm stays disabled until
you tick "I understand this places real orders on Coinbase with real money"; live resume asks the
same. The Live card's preflight lists, independently, Coinbase credential presence, whether a risk
policy is published, the available balance in the strategy's quote currency, whether the policy
allocates capital to this strategy, and the clock / user-order feed. Any source that cannot be read
shows **Unknown**. It is not a readiness verdict and does not gate arming; paper results are shown
as information only.

The execution worker
evaluates published paper and live deployments against closed venue candles about every 30 seconds.
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

Publishing a strategy is not deploying it. Deploy, pause, resume, and stop are explicit — on
the Run stage or through `thytrader-runtime` with the gates in [Safety](safety.md). Default stop is
**managed shutdown**: protective brackets stay and residual exposure stays in account-level risk
until the book is flat. Pass `--flatten` only when the operator asked to marketably exit then cancel
remainders. Pause still maintains attached-child protection; it does not reset daily-loss or
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
reports include redacted `books[]` (product, phase, side, protection — no quantities).

On-demand trades and published strategies use `entry.side` of `long` or `short`. CLI `--side`
defaults to `long`. A short is a Coinbase **spot** sell-to-open: live fails closed without
available base and never borrows. When stop and take-profit are known and trailing is off, live
attaches those exits to the entry; paper still uses synthetic exits. Command examples live in
[`skills/thytrader-runtime/SKILL.md`](../../skills/thytrader-runtime/SKILL.md).

Manage deployments at http://127.0.0.1:5175/deployments. Its paged inventory opens each
runtime's own `/deployments/{id}` detail; it does not send you to the draft editor. Detail is
anchored to the immutable published strategy fingerprint and separates mode/status, latest
completed-bar signal, current exposure and protection, paper fee assumptions, fill-ledger
performance, and paged orders/fills from historical backtest and research evidence for the same
version. A last signal is not a full per-bar decision history; no-trade conditions are stated only
when the runtime supplied that signal. Discretionary deployments have no published strategy source.
Published-only strategies have no editable draft; their Build stage shows the published definition
read-only. The workspace's `?version=` selects an exact published fingerprint, and an invalid
requested version must not start a different version. Detail links **Backtests of this version**
and **Decisions for this version** open the Test and Why stages for that fingerprint. Start a new
deployment on the Run stage; manage an existing one there or on its detail page.

### Why

A strategy's **Why** stage (`/strategies/{strategy_id}/why`) lists each deployment of the selected
version with its latest completed-bar signal ("No trade — conditions did not match", "could not be
evaluated", or matched) and the persisted trade reasons for that deployment (risk decision,
reconciled order and fills when the reason names one, notes). Full per-bar decision history is not
recorded yet; "no recorded trade rationale" does not mean the conditions failed. The UI must not
silently relabel a `BASE-USD` book’s PnL as USDC. Performance quote comes from the published
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
output. HTTP talks to loopback (`http://127.0.0.1:8200`) unless you pass `--local` on purpose.

```bash
uv run thytrader-operator health
uv run thytrader-research create-draft --confirm
uv run thytrader-research create-draft --template rsi-mean-reversion --confirm
uv run thytrader-research plan-study --file study.json
uv run thytrader-research submit-study --file study.json --confirm
uv run thytrader-research list-studies
uv run thytrader-playbook status
uv run thytrader-memory status
uv run thytrader-runtime show-settings
uv run thytrader-runtime set-settings --yolo-enabled true --yolo-tiers paper --confirm
```

| Lane | What it may do | Gate |
|---|---|---|
| `thytrader-operator` | Read-only diagnostics | none (never trades) |
| `thytrader-data` | Watchlist, ingest, gap-fill | `--confirm` on mutations; writes send installation Bearer when a token is resolvable |
| `thytrader-research` | Drafts, publish, backtests, composed studies, study catalog | `--confirm` on mutations; cannot deploy or trade |
| `thytrader-runtime` | Paper/live start, pause, resume, stop, on-demand place-order, risk policy, YAML settings, write-only Coinbase credentials | `--confirm`; live also `--i-understand-live`; `set-settings` and credential set/clear never YOLO |
| `thytrader-playbook` | Sequence data → research → optional paper | forwards `--confirm`; **never live** |
| `thytrader-memory` | Journals, why-trade review, sentiment/pattern hooks, monitor, notify, fail-closed train | `--confirm`; YOLO never covers this lane |

Ingest is a worker job (HTTP 202). The API dataset volume stays read-only. Missing candles are
never interpolated. One ingest queue keeps walking until `watch_complete` or a durable failure.
`inspect-gaps` may return `truncated` with a partial `gap_summary` when a server-side budget
stops the scan ([ADR 0072](../decisions/0072-catalog-health-bounded-gaps-self-complete-ingest.md)).

Command details live in the canonical skills under [`skills/`](../../skills/README.md). Do not
scrape logs, query PostgreSQL, or print `.env`.

For a numbered **portfolio visibility → data health → research** path (account balances, deployment
inventory, fingerprint copy, v4 backtests), see
[`docs/agent/portfolio-research-ops-playbook.md`](../agent/portfolio-research-ops-playbook.md).

### Read-only signal evaluation

An existing published research run that explicitly selects `thytrader-bar-signal-v1` can be replayed
against its exact verified strategy and Parquet dataset:

```bash
uv run thytrader-research-evaluate <run_fingerprint> --pretty
```

The command prints a deterministic completed-candle entry-condition trace and its SHA-256
fingerprint. It does not publish a run, create an order intent, apply cooldown, simulate entries or
exits, calculate PnL, persist results, or mutate trading state. Paper and live deployment is a
separate runtime (the workspace Run stage or `thytrader-runtime`), not this CLI.
