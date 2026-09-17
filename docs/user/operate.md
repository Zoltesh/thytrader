# Operate ThyTrader

You can drive ThyTrader from the browser, from confirmation-gated CLIs, or both. An authorized
agent can do the same loop **100%** through the shipped skills. Nothing requires an agent.

When an agent is operating a **running** instance, open [`ops/`](../../ops/README.md) rather than
the git root. Skills of record: [`skills/README.md`](../../skills/README.md).

## In the browser

After [setup](setup.md), open http://127.0.0.1:5175.

The first usable slice shows deterministic demo balances when Coinbase credentials are empty, and
live balances when both Coinbase variables are configured. That screen never submits an order.

### Strategies

Open http://127.0.0.1:5175/strategies when the stack is healthy. The library lists every strategy
identity with its market and timeframe, latest version, draft/published/archived status, the newest
backtest bound to any of its immutable versions, and its paper/live column: newest deployment
status per mode (`unavailable`, `running`, `paused`, or `stopped`) with a column legend.
`unavailable` means no runtime of that mode (not that the execution worker is missing). Clicking a
paper/live cell opens `/deploy` for that strategy.

From the library you can create the conservative reference draft, clone a published strategy into a
fresh draft identity (Clone stays ungated), import a complete strategy definition JSON as a new
draft, and archive an immutable publication after confirming the latest published version and
fingerprint.

Saves carry an opaque revision and reject stale browser tabs rather than overwriting newer edits.
The builder at `/strategies/{strategy_id}` opens any durable draft for full-schema editing with a
nested ALL/ANY/NOT rule tree and an inspector showing a plain-English summary, validation errors,
required warmup, unsaved state, and an explicit V1/V2 engine-support matrix. Every library row
opens the same read-only Insight panel; Research and Deploy are links to their own pages.

### Research

Open http://127.0.0.1:5175/research (or `/research?strategy=` from the library). Research explicitly
selects an immutable strategy version, verified dataset, evaluation period, initial capital,
maker/taker fees, fixed slippage, engine, and the V2 constant-spread stress assumption. Omitting
both evaluation dates on submit uses the common LTF+HTF (and extra-clock) covered intersection
rather than the LTF range alone. When Coinbase credentials are present, maker/taker fields prefill
from fee-tier suggested defaults and stay editable; demo or missing credentials leave those fields
blank rather than inventing a tier. It lists every stored result for each exact published version
and compares the latest result across versions; dataset and per-version result failures remain
visible without hiding strategy evidence. Result summaries report the published strategy clock,
including `2h` and `4h`, not a hardcoded `1h`.

**Validate & publish immutable version** (`POST /api/v1/strategies/{strategy_id}/publish`) atomically
consumes that mutable draft and records canonical strategy evidence; it does **not** start paper or
live trading. A published version can be archived from the library after confirmation: that appends
a permanent archive marker and hides it from active selection without changing its fingerprint or
canonical bytes. Backtests require a verified dataset fingerprint and remain deterministic research
artifacts.

### Paper and live

Open http://127.0.0.1:5175/deploy (or `/deploy?strategy=` from the library). The execution worker
evaluates published paper and live deployments against closed venue candles about every 30 seconds.
Paper simulates maker fills; live places Coinbase Advanced Trade spot orders when credentials
exist. Sub-hour live pauses unless the authenticated user-order feed is connected. Paper deploy and
new paper tickets accept optional maker/taker **assumptions** (UI Deploy/Trade, or
`thytrader-runtime --maker-fee-rate` / `--taker-fee-rate`). Omitted paper rates stay `0.001` /
`0.002`. They are documented fill costs, not observed Coinbase fees. Live rejects those fields and
keeps venue-recorded fees.

Publishing a strategy is not deploying it. Deploy, pause, resume, and stop are explicit — on
`/deploy` or through `thytrader-runtime` with the gates in [Safety](safety.md). Default stop is
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

### Journals

Open http://127.0.0.1:5175/journals for origin-attributed facts, lessons, and notes already stored
by experiential memory. Why-trade review is on [Memory](http://127.0.0.1:5175/memory) and
[Trade](http://127.0.0.1:5175/trade). Append still uses `thytrader-memory add-journal --confirm`.

### Operator chat

Open http://127.0.0.1:5175/chat. Paste **your LLM API key** (OpenAI or OpenAI-compatible). This is
not a Coinbase form — Coinbase keys stay on the separate secrets surface and never enter the
browser. The key is held in the API process only; restarting the API clears it.

The chat is an operator over the same gated skill lanes as `ops/`. Read-only diagnosis runs
immediately. Data, research, runtime, and memory mutations wait for in-app confirmation. Live start
and live place-order also need the understand-live checkbox. Paper start and paper on-demand orders
may include maker and taker fee assumptions; live Coinbase fees stay venue-recorded.
`uv run thytrader-operator chat-status` reports whether a key is configured; it never prints the
secret.

### Settings

Open http://127.0.0.1:5175/settings. YAML is the source of truth for non-secret knobs: YOLO on/off,
independent tiers (`data`, `research`, `paper`, `live`), log level, intervals, lookback, default
ingest product, and notify provider. Changes apply without restarting API or workers. Secrets stay
in ignored `.env`. Leftover `THYTRADER_YOLO_TIERS=paper` is valid. Live still needs
`--i-understand-live`. The Coinbase section beside that panel sets, rotates, or clears Advanced
Trade API secrets. Keys stay server-side. The form wipes before the request; GET never echoes them.
LLM keys stay on Chat.

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
separate `/deploy` runtime, not this CLI.
