---
name: thytrader-desk
description: >-
  Run ThyTrader agent desks through thytrader-desk: a cash pool, a product universe and caps
  that a model harness (Claude Code, Pi, Codex, Hermes, or any command) wakes on a cadence.
  Each wake ThyTrader writes a briefing, the model answers with one JSON decision (no_trade,
  open, close), and ThyTrader applies it to a fresh discretionary book under the caps and
  journals it. Desks are paper unless created live with --i-understand-live and reserved in the
  risk policy's desk_allocations. Use when the user asks to create, wake, schedule, pause,
  update (harness, model, cadence, mandate, caps), score or review an agent desk, or to let a
  model trade. Mutations require --confirm. YOLO
  never covers this lane. Never arms strategy bots or touches portfolios.
---

# ThyTrader agent desk

Discretionary trading by a model, inside hard caps
([ADR 0147](../../docs/decisions/0147-agent-desk.md); live desks
[ADR 0148](../../docs/decisions/0148-live-agent-desks.md)). This skill is not an extension of
`thytrader-runtime` (which places on-demand orders) or `thytrader-portfolio` (which manages strategy
sleeves). Desks are paper by default; a live desk is described under "Live desks" below.

HTTP-only against the loopback API (base URL and installation Bearer auth: [shared
rules](../README.md#shared-rules-every-lane)). Every command checks the ops contract first
(`thytrader-ops-contract-v111`, schema revision `0082`); on a mismatch, rebuild the image. Desk
briefings and decisions are `thytrader-agent-desk-v1` (`agent_desk_contract` in the ops contract).
`create`, `update`, `wake`, `record` and `status` need `--confirm`; YOLO never skips it.

## How a desk works

- **Pool.** `starting_cash` in one quote currency (`USD`, `USDC`, `USDT`). Every product in the
  universe (up to 20 spot ids) must end in that quote (`DESK_PRODUCT_INVALID` otherwise).
- **One trade, one book.** An `open` funds a **new** discretionary book (origin `agent`) with
  `size_fraction × equity` from the pool. The order notional is 98% of the allocation, so the book
  can pay fees. When the book is flat and stopped, its final cash returns to the pool at the next
  briefing, wake or score (settlement).
  A closed book that still rests a stray exit order (for example a take-profit left after its
  stop filled) is flattened by the desk itself once its evidence is final (fills applied and
  netting to zero, accounting complete, live cancels settled, every venue child imported): the
  API logs `desk_book_stranded_orders`, the worker cancels the order on its next cycle, and the
  book settles at a later read. If the stray order fills before the cancel, its cash is applied
  but no position can be built on the closed book: the book keeps a fault and holds settlement
  until an operator reconciles or repairs it (nothing exits it automatically). It stays stopped
  if the desk had already flattened it; a book still running is paused instead, and resuming
  it clears that fault while settlement stays held, so repair it before resuming. `desk_book_settlement_held` (info) means a flat
  book's evidence is not final yet: a live cancel inside its settlement watermark, a venue child
  not imported yet, fill economics not applied, inventory the position does not show, or fills
  that do not net to zero. A resting order that could add inventory, or whose venue state is
  pending or unknown, keeps the book open. A running or paused book settles through a stop bound
  to the revision the desk checked; a stopped book without a fault settles with no write once a
  re-read shows the same revision and the same final evidence; a stopped book with a fault and
  nothing resting is stopped again to clear it, keeping a flatten command it carries (a book
  still resting a stray exit waits for the worker to cancel it, since the desk cannot). `desk_book_stop_deferred`
  means the book changed or the write lost a race, and the next read re-checks it. The desk acts on that evidence, not on fault text: a fault left
  on a book whose evidence is final (for example an old read failure or data-gap pause) is
  cleared by that stop, so it never locks pool cash; a failed venue read leaves the order unknown
  and holds the book. `desk_book_read_deferred` means a book could not be read: its settlement
  waits for the next read and its siblings still settle, but if it is still unreadable when the
  desk state is built, that briefing, wake or score fails.
- **Fees** are the account's Coinbase maker/taker rates, read from the account at each order (the
  same source as paper starts, ADR 0122). With no readable account the open is refused; nothing is
  assumed.
- **Caps** (defaults): `max_open_positions` 3, `max_position_fraction` 0.25,
  `max_entries_per_day` 8 (UTC), `daily_loss_fraction` 0.05 of the UTC day-open equity. One open
  book per product. A paused or retired desk journals wakes but never trades.
- **Every wake is journaled**, including `no_trade`, malformed answers (`invalid`), refusals
  (`refused` with a code), harness failures (`error`, `DESK_HARNESS_FAILED`) and skipped wakes
  (`skipped`, `DESK_WAKE_UNCHANGED`: no harness ran because nothing changed; see "Skipped
  wakes").

## Commands

Run from the repository root. Output is JSON unless noted.

```bash
uv run thytrader-desk harnesses                       # built-in harness command lines
uv run thytrader-desk create --name "BTC/ETH desk" --quote-currency USDC --starting-cash 10000 \
  --product BTC-USDC --product ETH-USDC --product SOL-USDC \
  --harness claude --cadence-minutes 30 [--model MODEL] [--mandate TEXT] \
  [--max-open-positions N] [--max-position-fraction F] [--max-entries-per-day N] \
  [--daily-loss-fraction F] [--timeframe 5m] [--mode live --i-understand-live] --confirm
uv run thytrader-desk list
uv run thytrader-desk show DESK_ID                    # desk + every book it funded
uv run thytrader-desk briefing DESK_ID [--prompt]     # --prompt prints only the prompt text
uv run thytrader-desk wake DESK_ID [--harness NAME] [--model M] [--command CMD] \
  [--timeout 600] [--always] --confirm
uv run thytrader-desk update DESK_ID [--harness NAME] [--model M | --clear-model] \
  [--cadence-minutes N] [--mandate TEXT] [--name NAME] [--max-open-positions N] \
  [--max-position-fraction F] [--max-entries-per-day N] [--daily-loss-fraction F] \
  [--expected-revision N] [--i-understand-live] --confirm
uv run thytrader-desk record DESK_ID --fingerprint sha256:... --response-file FILE|- \
  --harness NAME [--model M] --config-fingerprint sha256:... \
  [--material-fingerprint sha256:...] [--runner-fingerprint sha256:...] --confirm
uv run thytrader-desk journal DESK_ID [--limit 20]
uv run thytrader-desk score DESK_ID
uv run thytrader-desk status DESK_ID active|paused|retired --confirm   # kept in changes, audited; same status: no write
uv run thytrader-desk schedule DESK_ID [--style systemd|cron] [--command CMD]
```

HTTP: `POST|GET /api/v1/desks`, `GET|PATCH /api/v1/desks/{id}`, `GET .../briefing`,
`POST|GET .../wakes` (`limit` 1–500), `POST .../wakes/skip`, `GET .../score`,
`POST .../status`.

## Waking a desk

`wake` takes a per-desk lock (an overlapping wake returns `{"skipped": true, ...}` and journals
nothing), asks ThyTrader whether the wake can be skipped (below; `--always` never asks), then
reads the briefing, runs the harness with the prompt, and posts the answer with the briefing's
`fingerprint`, `material_fingerprint` and `config_fingerprint` and the runner's
`runner_fingerprint` (a digest of the harness name, command template and model). The harness and
model come from that briefing (its `harness` / `model`, the desk's configuration it was built
under), so a `desk update` landing mid-wake never runs the old model against the new
configuration. Exit 0 means the wake was journaled; read `outcome` / `outcome_code` / `detail` for
what happened. Any failure to set up or run the harness (an unknown harness name
`DESK_HARNESS_UNKNOWN`, a blank `--command` `DESK_HARNESS_INVALID`, a misquoted one or a binary
that cannot start `DESK_HARNESS_FAILED`) is journaled as an `error` wake. `config_fingerprint` is
required on every recorded answer: an `open` (or `no_trade`) answering a briefing whose
configuration has since changed is journaled `refused` `DESK_CONFIG_CHANGED` and not applied; a
`close` is still applied, since it only reduces risk. The `material_fingerprint` is kept only if
the answered briefing is still the desk's current one when the answer is recorded, recent
answered wakes included (otherwise the wake stores none and the next wake asks the model): an
answer that missed a refusal, failure or refund journaled while it thought never anchors a skip.
A live desk takes no `--harness` or `--model` flag (`DESK_LIVE_OVERRIDE_REFUSED`, even one equal
to the desk's; journaled as an `error` wake, so `DESK_WAKES_FAILING` names it): change who trades
it with `update ... --i-understand-live`. `--command` is refused too when the live desk's harness
is built in, judged against the briefing the wake answers (so an update to a built-in harness
mid-wake refuses the old command), and `schedule --command` refuses to print such a timer; it runs only a live desk whose configured harness is
custom, under that harness name and the desk's model (ThyTrader cannot see which program a command
runs, so keep the timer's command in step with the desk's acknowledged harness). The API refuses an
open or no_trade answer on a live desk that names another harness or model (`refused`
`DESK_LIVE_IDENTITY_MISMATCH`; an empty model equals none); a close is applied. If the skip request
itself fails, the wake asks the model anyway.
Wakes are listed newest first in journal order (the server's UUIDv7 `wake_id`, minted strictly
increasing under the desk's lock; a recovered open keeps its reservation's id); `started_at` is
the runner's own clock, must carry a timezone (422 otherwise) and is clamped to the server's now.

### Skipped wakes

The briefing's market rows come from closed hourly candles, so a desk woken every 30 minutes
would otherwise ask its model about the same hour twice. `POST .../wakes/skip` (body `harness`,
`model`, `runner_fingerprint`) builds the briefing under the desk's lock and journals a `skipped`
wake (action and outcome `skipped`, code `DESK_WAKE_UNCHANGED`, no thesis, no model call) only
when all of these hold; otherwise it returns `skipped: false` with the `reason` and the harness
runs:

- the newest wake (journal order) was an applied `no_trade` or itself a skip
  (`DESK_WAKE_RETRY` after an error, invalid answer or refusal; `DESK_WAKE_ACTED` after an
  applied open or close);
- the same harness, model and runner fingerprint would answer (`DESK_WAKE_HARNESS_CHANGED`);
- the desk holds no open book and had no pending open before this read (`DESK_WAKE_HOLDING`): a
  held book is always shown to the model. A crashed open that any read refunds is journaled
  `refused` `DESK_OPEN_REFUNDED` (a linked one `DESK_OPEN_RECOVERED`), so it is never skipped
  past;
- the newest wake recorded a material fingerprint (`DESK_WAKE_UNANCHORED` otherwise, for
  example a `record` without `--material-fingerprint`) and it equals the briefing's
  (`DESK_WAKE_CHANGED`). The `material_fingerprint` covers the briefing without its generation
  time and recent wakes (market rows, cash, caps, headroom, entry block, fee rates, positions)
  plus the UTC date, the prompt instructions, the desk's configured harness, model, mandate
  and caps (`config_fingerprint`) and `due_since` (moved by a resume or a new cadence), so a
  new day, a new prompt, a resume or a `desk update` always wakes the model;
- the last answered wake was journaled within the last six hours, by server time
  (`DESK_WAKE_DUE`).

Skipped wakes never appear in the briefing's `recent_wakes`, which reads answered wakes only, so
no run of skips crowds earlier theses out. Scheduled wakes need nothing new: the scheduled
`wake ... --confirm` skips by itself.

Built-in harnesses (`harnesses` prints the exact argv):

| Harness | How it runs | Tools |
| --- | --- | --- |
| `claude` | `claude -p` with the prompt on stdin | `--tools ""`, no MCP, no settings sources |
| `pi` | `pi -p` with the prompt on stdin | `--no-tools`, no extensions, skills or context files |
| `codex` | `codex exec` with the prompt on stdin | read-only sandbox (Codex cannot turn tools off) |
| `hermes` | `hermes --ignore-rules -z PROMPT` | Hermes keeps its own toolsets |

Any other harness: `--command "mytool run --model {model}"` (prompt on stdin) or put `{prompt}` in
the command to pass it as an argument. Each run starts in a new empty directory, and
`THYTRADER_*`/`COINBASE_*` variables are removed from its environment. ThyTrader applies only the
answer, under the caps.

**Isolation differs by harness.** `claude` and `pi` run with no tools, so the model can only
answer. `codex` and `hermes` (and a `--command` harness with tools) run as your user: they can
read any file that user can, including `.env`, the credentials directory and the installation
token, and Hermes can also act. Prefer `claude` or `pi` for an unattended desk; use the others
only on a machine where that access is acceptable.

An agent that should think with its own tools can instead read `briefing`, decide, and `record`
its answer with the briefing's `fingerprint` and `config_fingerprint` (and its
`material_fingerprint` plus a stable `--runner-fingerprint` of its own, so unchanged later skip
requests by the same runner can skip; skips need the same runner fingerprint).

## The decision contract

The answer must contain exactly one JSON object (a fenced `json` block or bare braces):

```json
{"action": "no_trade", "thesis": "why"}
{"action": "open", "thesis": "why", "product_id": "BTC-USDC", "side": "long",
 "size_fraction": "0.1", "entry": "market", "stop_price": "...", "take_profit_price": "..."}
{"action": "close", "thesis": "why", "deployment_id": "BOOK_ID"}
```

`entry` is `market` or `limit` (`limit_price` required; the order is post-only). `side` is `long`
or `short`; a spot short is a paper simulation (no borrow). Stops and targets go on the book's
exits like any discretionary order.

Refusal codes: `DESK_DECISION_INVALID`, `DESK_NOT_ACTIVE`, `DESK_PRODUCT_NOT_IN_UNIVERSE`,
`DESK_PRODUCT_OCCUPIED`, `DESK_MAX_POSITIONS`, `DESK_MAX_ENTRIES_TODAY`, `DESK_EQUITY_UNKNOWN`,
`DESK_DAILY_LOSS`, `DESK_POSITION_TOO_LARGE`, `DESK_CASH_SHORT`, `DESK_UNKNOWN_BOOK`, and
`DESK_SHORT_LIVE_UNSUPPORTED`, `DESK_LIVE_LIMIT_ONLY`, `DESK_DUPLICATE_ANSWER`,
`DISCRETIONARY_NOT_ALLOCATED` (a live desk
the policy does not reserve), and execution refusals from the discretionary path, led by their own code or `DESK_ORDER_REFUSED`
with the reason in `detail` (for example paper fees unavailable, or the risk policy's
`paper_capital_quote` / single-quote paper capital and running-slot caps, which desk books count
against like any paper book). Harness
failures: `DESK_HARNESS_NOT_FOUND`, `DESK_HARNESS_TIMEOUT`, `DESK_HARNESS_EXIT`,
`DESK_HARNESS_EMPTY` (journaled as `error`). `DESK_OPEN_RECOVERED` marks an applied open
whose own journal row was lost to an interrupted write and rebuilt on recovery.

## Live desks

A live desk spends real money from the account. Two steps, both the operator's decision:

1. Create it live (HTTP `mode: "live"`, `i_understand_live: true`; 428 without the flag):
   `uv run thytrader-desk create ... --mode live --i-understand-live --confirm`. The desk records
   when live was acknowledged. YOLO never covers this.
2. Reserve its capital in the risk policy (`thytrader-runtime` skill): while any strategy or
   desk allocation is in force, a live desk's orders are refused `DISCRETIONARY_NOT_ALLOCATED`
   until the policy lists it in `desk_allocations` (`set-risk-policy ... --desk-allocation
   DESK_ID:QUOTE`, `QUOTE` in the policy's quote currency; the desk must trade that quote). The
   reservation caps the desk's total: the allocations of its open books plus each new
   allocation (`ALLOCATION_EXCEEDED` beyond it). Give the desk a pool no larger than its reservation.
   `set-risk-policy` publishes a whole replacement policy, so carry every existing field and
   allocation forward.

A live desk is long only (`DESK_SHORT_LIVE_UNSUPPORTED`) and enters with post-only limit orders
only (`DESK_LIVE_LIMIT_ONLY`): a limit fills at its price or better, so an order never spends more
than its allocation. It uses the live discretionary path:
the live broker, the venue's remaining quote, the account caps and breakers, and a stop and
target attached at the venue. Desk books are valued by their own fills (allocation, plus sales,
minus purchases and fees, plus held quantity at the mark), never by a live book's shared cash.
A book settles only when every recorded fill is applied and matches the position, and, live,
every cancel has passed its settlement watermark (ADR 0141); a late fill is not expected after
that. Held books are marked at the newer of the hourly close and their own clock's current close
(if neither is current, equity is unknown and entries stop). Each answer is one order: the
order is keyed on the briefing fingerprint, and an answer whose order already exists is refused
`DESK_DUPLICATE_ANSWER` before any cash moves. An open reserves its book's id before placing, so an interrupted
open links exactly that book (`DESK_OPEN_RECOVERED`) or refunds the reservation.
`show`, `journal` and `score` work as on paper; `mode` is on the desk and in the briefing, and the
model's prompt says it is trading real money. Pause a live desk with `status DESK_ID paused
--confirm`; its open books keep their venue stops and targets.

## Scheduling

ThyTrader does not run the harness itself. `schedule` prints a systemd user service/timer pair (or a
crontab line for cadences dividing 60) that runs `thytrader-desk wake DESK_ID --confirm` from the
current directory with the current `PATH` and the resolved `--base-url`. A desk whose harness is
not built in needs `schedule DESK_ID --command "..."`, which is carried into the wake command.
Show it to the user; install it only when they ask.

## Scoring

`score` reports the desk's equity return since creation, wake counts by outcome over the newest
20,000 wakes (`wakes` counts journaled wakes, `answered_wakes` those that ran the model, and
`wakes_by_outcome.skipped` the rest), books opened,
settled and settled winners, and two buy-and-hold benchmarks over the same window, each paying the
account taker fee on entry and exit: `btc_buy_and_hold` (`BTC-<quote>`) and
`universe_equal_weight`. `beats_btc` / `beats_universe` are `null` while either side is unknown.
Paper results are evidence, not a promise: judge a desk over weeks and many trades.

## Changing a desk

`update` (HTTP `PATCH /api/v1/desks/{id}`) changes the harness, model (`--clear-model` unsets
it), cadence, mandate, caps (named caps merge into the current ones) or name, validated like a
new desk and saved under a new revision; `--expected-revision` refuses (`DESK_CONFLICT`, 409) if
the desk moved on. The change is recorded on the desk itself in the same write: `changes` keeps
the newest 20 (`at`, `revision`, and old and new values per field; a mandate by digest and
length). It is also appended to the audit log as `desk_updated` (with `mode` and
`live_acknowledged`); that audit write is best effort, the desk's `changes` are not. A new cadence
also restarts the stall clock (`due_since`). Mode, quote currency, pool, universe and book timeframe are fixed. On a
live desk, changing the harness, model, mandate or caps needs `--i-understand-live` (HTTP
`i_understand_live: true`; 428 without it), like creating it; the name and cadence do not.
`DESK_UPDATE_EMPTY` means nothing would change; `DESK_UPDATE_INVALID` names the bad field. A new
cadence does not move an installed timer: print a new one with `schedule` and reinstall it.
Prefer `update` over a `wake --model` override in the timer, so the desk row, the journal and
the score name the model that actually trades.

## Alerts

The execution worker reads every desk and its newest wakes each cycle and raises durable alerts
(subject `desk:<desk_id>`, scope `desk`; `thytrader-operator alerts`):

- `DESK_WAKES_FAILING`: the newest 3 wakes (journal order) all failed (`error` or `invalid`),
  for example an expired harness login or no credits, or were refused `DESK_CONFIG_CHANGED` /
  `DESK_LIVE_IDENTITY_MISMATCH` (the runner answering is not the desk's, which repeats until
  you fix the timer or the desk). The alert names the failure codes only
  (for example `DESK_HARNESS_FAILED, DESK_HARNESS_EXIT`), never the harness output; read the
  `journal` for the detail.
- `DESK_WAKES_STALLED`: no wake (a skipped wake counts) for 3 cadences plus 10 minutes since
  the newest wake's journal time, or since the desk became due to wake (`due_since`: created,
  resumed with `status ... active`, or given a new cadence). Briefing, score and settlement
  writes never restart it. Its timer probably stopped.

Both are `critical` for a live desk (delivered like any critical alert) and `warning` for a
paper desk, and resolve once the desk answers again or is paused or retired. They only record
and warn; nothing pauses the desk. The worker judges them once per cycle within a 10-second
budget; a slow or unreadable desk store leaves them unknown (neither raised nor resolved).

## Limits

- A desk's mode is fixed at creation. To trade a paper desk's idea live, create a live desk.
- The briefing's market rows are hourly summaries from stored candles; a product with no data shows
  `unavailable_reason`.
- A desk book is an ordinary discretionary book: it appears in `thytrader-runtime list` and the
  browser. A desk `close` flattens it. A plain runtime/browser stop is a managed stop that keeps
  the position, so the book settles into the pool only once it is flat; to end it now use
  `uv run thytrader-runtime stop BOOK_ID --flatten --confirm`.
- An open reserves the pool cash (and the new book's id) first, places the order, then links the
  book and journals the wake in one write. If the API dies in between, the next briefing, wake or
  score links the book with that id (journaled `DESK_OPEN_RECOVERED`) or refunds the reservation.
