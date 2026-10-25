# 0147: Agent desk: scheduled LLM discretionary paper trading

- Status: Accepted
- Date: 2026-10-20
- Relates to: [0039](0039-on-demand-discretionary-trades.md) (discretionary books),
  [0091](0091-portfolio-deployment-limits-and-manager-proposals.md) (an LLM loop outside
  ThyTrader), [0122](0122-paper-fees-default-to-account-rates.md) (paper fees from the account),
  [0087](0087-per-bar-decision-timeline.md) (decision journal)

## Context

Strategies and studies test fixed rules. Some judgment (news, listings, regime shifts) does not
fit a rule, and current models can read a market summary and decide. Whether that judgment has
an edge after fees is an empirical question, so it must be measured like a strategy: on paper,
with every decision recorded, scored against baselines before any live money.

Discretionary books (ADR 0039) already place a paper order with stop, target, `origin=agent` and
a thesis note. What is missing: one capital pool across trades and products, a record of every
wake (including "no trade"), a price read an agent can use, a scheduler, and a desk-level score.

## Decision

1. **A desk** is a named paper trading mandate: starting capital, quote currency, product
   universe, cadence, harness, and caps (max open positions, max position fraction, max new
   entries per UTC day, daily loss fraction). Paper only in this slice; a live desk is refused.
2. **Capital.** The desk keeps one cash pool. Each entry moves `size_fraction` × equity from the
   pool into a fresh discretionary paper book (`fresh_book`, never a reused one) and orders 98%
   of it, leaving fee headroom. When the book is flat (stop, target or a desk close) the desk
   stops it and returns its cash. Desk equity is pool cash plus every open book marked at the
   latest closed 1h candle. The desk holds at most one book per product
   (`DESK_PRODUCT_OCCUPIED`); an occupied discretionary book outside the desk on the same
   product also blocks the entry, as for any discretionary order. Paper fee rates come from the
   Coinbase account, as for every paper book (ADR 0122); without them the entry is refused and
   no rate is assumed.
3. **Briefing in, decision out.** Each wake the runner builds one compact briefing (desk state,
   open positions with marks, recent wakes, and per product: last price, 1h/4h/24h/7d returns,
   ATR, volume versus its average) and sends it to the harness as plain text. The model answers
   with one JSON decision: `no_trade`, `open` (product, side, equity fraction, entry kind, stop,
   target, thesis) or `close` (book, thesis). ThyTrader validates it against the caps and applies
   it. Only the answer is applied. Built-in harness templates turn the harness's tools off where
   it allows (Claude Code `--tools ""`, Pi `--no-tools`); Codex runs in a read-only sandbox and
   Hermes keeps its toolsets. Every run starts in a new empty directory with `THYTRADER_*` and
   `COINBASE_*` variables removed. That is not a sandbox: a harness with tools runs as the
   operator's user and can read `.env`, the credentials directory and the installation token, so
   only the tool-less templates (Claude Code, Pi) keep the model to answering; the skill says so
   and recommends them for unattended desks. An agent
   that wants its own tools can instead read the briefing and `record` an answer against the
   briefing fingerprint.
4. **Every wake is journaled** in `desk_wakes`: time, harness and model, briefing fingerprint,
   the raw decision, the applied outcome (or the refusal code), the thesis, desk equity and the
   linked book. A malformed (`invalid`) or refused answer, or a harness failure (`error`,
   `DESK_HARNESS_FAILED`), is recorded as such and never retried silently.
5. **Harnesses** are command templates: Claude Code (`claude -p`), Pi (`pi -p`), Codex
   (`codex exec`) and Hermes (`hermes -z`) are built in; any other harness (for example
   opencode) is a `--command` line with `{prompt}`/`{model}` placeholders. The runner
   (`thytrader-desk wake`) holds a per-desk lock (no overlapping wakes) and a timeout. Scheduling
   is the host's (a systemd user timer or cron line printed by `thytrader-desk schedule`).
6. **Scoring.** `thytrader-desk score` reports desk return since creation, wakes by outcome,
   books opened and settled, and settled winners, against buy-and-hold `BTC-<quote>` and an
   equal-weight buy-and-hold of the universe over the same window, each paying the account
   taker fee on entry and exit. A desk earns live consideration only by beating those over a
   pre-registered window. Drawdown and per-desk fee totals are follow-ups.

## Consequences

- New tables `desks`, `desk_wakes`, `desk_books` (Alembic 0080); ops contract bump.
- Agents use the desk through CLI/HTTP like every other surface; a browser flow can follow once
  a desk shows promise.
- Discretionary books stay single-trade from the desk's point of view; per-order amend and
  partial closes are out of scope.
- A desk book that turns flat between wakes is stopped and settled at the next briefing, wake
  or score read; until then it is a flat running discretionary book. Evidence is final when
  every fill is applied and the fills net to zero, accounting is complete, live cancels are
  settled and every venue child a parent names is imported. A flat book with final evidence
  that still rests only stray exits (confirmed-open orders on the reducing side under an exit
  intent, or venue protection attached to the entry) is flattened first: the worker cancels the
  stray order on its next cycle and a later read settles the book. A stray fill that lands
  before the cancel has its cash applied but builds no position on the closed book, so the book
  pauses with a fault and holds settlement for reconciliation or repair. A running or paused
  book settles through a stop bound to the revision the desk checked, so a fault or fill written
  after that read refuses it instead of being cleared; a stopped book without a fault settles
  with no write once a re-read confirms its revision (no write to race the worker's fenced
  ones), and a stopped book still flattening is left to the worker's flatten settlement. A
  refused or lost write is logged (`desk_book_stop_deferred`) and re-checked at the next read. The desk acts on that evidence, not on fault text: a fault left on
  a book whose evidence is final is cleared by its stop (2026-10-25: holding on fault text
  stranded books whose faults the worker never clears), while a failed venue read leaves an
  order unknown and holds the book. Those reads therefore
  write (settlement is deterministic bookkeeping, not a decision), while trading decisions stay
  behind authenticated POSTs.
- Pool cash and book links change together: every operation on one desk (show, briefing
  settlement, wake, score, status) holds that desk's lock in the API process. An open reserves
  the cash with a `pending_open` marker in the pool debit's write, places the order under the
  idempotency key `desk:{desk}:{wake}`, then clears the marker, links the book and journals the
  wake in one transaction. A marker left by a failure is resolved on the next read: the book
  found under the key (or, when its order was never recorded, the discretionary paper book on
  that product funded with exactly that allocation within five minutes of the reservation) is
  linked, journaled `DESK_OPEN_RECOVERED`; otherwise the cash is refunded.
  Settlement marks books settled and credits the pool in one transaction. A close is journaled
  after the flatten request; if that journal write fails the close still happened (the book's own
  runtime audit records it) and the wake row is missing: a known gap, with no cash effect.
- Discretionary entry admission now marks every held product of peer books (last close), as
  fleet entry health does; before, any other book's inventory left daily-loss evidence
  unavailable and refused the entry.
- The daily loss baseline is the last equity journaled before midnight, however old (else, on a
  desk's first day, the first equity seen). Market rows whose newest closed hourly bar is over
  two hours stale carry no price, so an open book on them leaves equity unknown and blocks
  entries.
- HTTP: `/api/v1/desks` (create, list, show, briefing, wakes, score, status); CLI
  `thytrader-desk`; skill `skills/thytrader-desk`; ops contract v107 advertises
  `agent_desk_contract` `thytrader-agent-desk-v1`.
