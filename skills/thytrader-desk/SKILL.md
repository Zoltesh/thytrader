---
name: thytrader-desk
description: >-
  Run ThyTrader paper agent desks through thytrader-desk: a paper cash pool, a product
  universe and caps that a model harness (Claude Code, Pi, Codex, Hermes, or any command)
  wakes on a cadence. Each wake ThyTrader writes a briefing, the model answers with one JSON
  decision (no_trade, open, close), and ThyTrader applies it to a fresh discretionary paper
  book under the caps and journals it. Use when the user asks to create, wake, schedule,
  pause, score or review an agent desk, or to let a model day-trade on paper. Mutations
  require --confirm. YOLO never covers this lane. Paper only: never live, never arms, never
  touches strategy bots or portfolios.
---

# ThyTrader agent desk

Paper discretionary trading by a model, inside hard caps
([ADR 0147](../../docs/decisions/0147-agent-desk.md)). This skill is not an extension of
`thytrader-runtime` (which places on-demand orders) or `thytrader-portfolio` (which manages strategy
sleeves). A desk never trades live; there is no live flag.

HTTP-only against the loopback API (base URL and installation Bearer auth: [shared
rules](../README.md#shared-rules-every-lane)). Every command checks the ops contract first
(`thytrader-ops-contract-v107`, schema revision `0080`); on a mismatch, rebuild the image. Desk
briefings and decisions are `thytrader-agent-desk-v1` (`agent_desk_contract` in the ops contract).
`create`, `wake`, `record` and `status` need `--confirm`; YOLO never skips it.

## How a desk works

- **Pool.** `starting_cash` in one quote currency (`USD`, `USDC`, `USDT`). Every product in the
  universe (up to 20 spot ids) must end in that quote (`DESK_PRODUCT_INVALID` otherwise).
- **One trade, one book.** An `open` funds a **new** discretionary paper book (origin `agent`) with
  `size_fraction × equity` from the pool. The order notional is 98% of the allocation, so the book
  can pay fees. When the book is flat and stopped, its final cash returns to the pool at the next
  briefing, wake or score (settlement).
- **Fees** are the account's Coinbase maker/taker rates, read from the account at each order (the
  same source as paper starts, ADR 0122). With no readable account the open is refused; nothing is
  assumed.
- **Caps** (defaults): `max_open_positions` 3, `max_position_fraction` 0.25,
  `max_entries_per_day` 8 (UTC), `daily_loss_fraction` 0.05 of the UTC day-open equity. One open
  book per product. A paused or retired desk journals wakes but never trades.
- **Every wake is journaled**, including `no_trade`, malformed answers (`invalid`), refusals
  (`refused` with a code) and harness failures (`error`, `DESK_HARNESS_FAILED`).

## Commands

Run from the repository root. Output is JSON unless noted.

```bash
uv run thytrader-desk harnesses                       # built-in harness command lines
uv run thytrader-desk create --name "BTC/ETH desk" --quote-currency USDC --starting-cash 10000 \
  --product BTC-USDC --product ETH-USDC --product SOL-USDC \
  --harness claude --cadence-minutes 30 [--model MODEL] [--mandate TEXT] \
  [--max-open-positions N] [--max-position-fraction F] [--max-entries-per-day N] \
  [--daily-loss-fraction F] --confirm
uv run thytrader-desk list
uv run thytrader-desk show DESK_ID                    # desk + every book it funded
uv run thytrader-desk briefing DESK_ID [--prompt]     # --prompt prints only the prompt text
uv run thytrader-desk wake DESK_ID [--harness NAME] [--model M] [--command CMD] \
  [--timeout 600] --confirm
uv run thytrader-desk record DESK_ID --fingerprint sha256:... --response-file FILE|- \
  --harness NAME [--model M] --confirm
uv run thytrader-desk journal DESK_ID [--limit 20]
uv run thytrader-desk score DESK_ID
uv run thytrader-desk status DESK_ID active|paused|retired --confirm
uv run thytrader-desk schedule DESK_ID [--style systemd|cron]
```

HTTP: `POST|GET /api/v1/desks`, `GET /api/v1/desks/{id}`, `GET .../briefing`,
`POST|GET .../wakes` (`limit` 1–500), `GET .../score`, `POST .../status`.

## Waking a desk

`wake` takes a per-desk lock (an overlapping wake returns `{"skipped": true, ...}`), reads the
briefing, runs the harness with the prompt, and posts the answer. Exit 0 means the wake was
journaled; read `outcome` / `outcome_code` / `detail` for what happened.

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
its answer with the briefing's `fingerprint`.

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
execution refusals from the discretionary path, led by their own code or `DESK_ORDER_REFUSED`
with the reason in `detail` (for example paper fees unavailable, or the risk policy's
`paper_capital_quote` / single-quote paper capital and running-slot caps, which desk books count
against like any paper book). Harness
failures: `DESK_HARNESS_NOT_FOUND`, `DESK_HARNESS_TIMEOUT`, `DESK_HARNESS_EXIT`,
`DESK_HARNESS_EMPTY` (journaled as `error`).

## Scheduling

ThyTrader does not run the harness itself. `schedule` prints a systemd user service/timer pair (or a
crontab line for cadences dividing 60) that runs `thytrader-desk wake DESK_ID --confirm` from the
current directory with the current `PATH`. Show it to the user; install it only when they ask.

## Scoring

`score` reports the desk's equity return since creation, wake counts by outcome, books opened,
settled and settled winners, and two buy-and-hold benchmarks over the same window, each paying the
account taker fee on entry and exit: `btc_buy_and_hold` (`BTC-<quote>`) and
`universe_equal_weight`. `beats_btc` / `beats_universe` are `null` while either side is unknown.
Paper results are evidence, not a promise: judge a desk over weeks and many trades.

## Limits

- Paper only. Promoting a desk to live is not implemented.
- The briefing's market rows are hourly summaries from stored candles; a product with no data shows
  `unavailable_reason`.
- A desk book is an ordinary discretionary paper book: it appears in `thytrader-runtime list` and the
  browser. A desk `close` flattens it. A plain runtime/browser stop is a managed stop that keeps
  the position, so the book settles into the pool only once it is flat; to end it now use
  `uv run thytrader-runtime stop BOOK_ID --flatten --confirm`.
- An open places the order, then debits the pool and links the book in one write. If the API
  dies in between, the paper book exists but is not the desk's (its note starts with the desk
  name): stop it with `--flatten`; the pool never paid for it.
