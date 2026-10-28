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
  keeps a fault (stopped if the desk had already flattened it, otherwise paused; resuming a
  paused one clears the fault while settlement stays held) and holds settlement for
  reconciliation or repair. A
  running or paused book settles through a stop bound to the revision the desk checked, so a
  fault or fill written after that read refuses it instead of being cleared; a stopped book
  without a fault settles with no write once a re-read confirms its revision and its final
  evidence (no write to race the worker's fenced ones), and a stopped book with a fault and
  nothing resting is stopped again to clear it, keeping a flatten command it carries, so it
  never waits on a worker pass that may not run (a stray exit still resting waits for the
  worker's cancel; the desk has no cancel path). A refused or lost write is logged (`desk_book_stop_deferred`)
  and re-checked at the next read. The desk acts on that evidence, not on fault text: a fault left on
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

## Amendment (2026-10-28): skipped wakes, desk updates and desk wake alerts

A week of desks woken every 30 minutes showed three gaps. Nearly half the wakes (461 of 975)
re-read an hourly bar the desk had already answered, paying for a model call that could not see
anything new. A harness that kept failing (an expired provider credit) journaled `error` wakes
for about 18 hours with no alert. And a desk's model could only be switched by a `--model`
override in its timer, so the desk row, the briefing header and the score named the wrong model.

1. **Skipped wakes.** The briefing gains a `material_fingerprint`: the digest of the briefing
   without its generation time, fingerprints, prompt and recent wakes (market rows, desk view and
   positions), plus the UTC date, the prompt instructions and a `config_fingerprint` of the
   desk's harness, model, mandate, caps and mode. `POST /api/v1/desks/{id}/wakes/skip` builds the
   briefing under the desk's lock and journals a `skipped` wake (action and outcome `skipped`,
   `DESK_WAKE_UNCHANGED`, no response, no thesis) only when the newest wake in journal order was
   an applied `no_trade` or a skip by the same harness, model and runner fingerprint (a digest of
   the harness name, command template and model), its material fingerprint equals the current
   one, the desk holds no open book and had no pending open before the read, and the last
   answered wake was journaled within six hours. Otherwise it returns the reason and
   `thytrader-desk wake` runs the harness. An error, invalid answer, refusal or applied action is
   never skipped past; `wake --always` never asks. Skipped wakes stay out of the briefing's recent
   wakes (read as answered wakes only) and the score adds `answered_wakes`.
2. **Server time and stale answers.** Wakes are listed in journal order (the server's UUIDv7
   `wake_id`, minted strictly increasing in the one API process), and recency is the server-set
   `finished_at`; a runner's `started_at` must carry a timezone and is clamped to now. The runner
   takes its harness and model from the briefing it answers (which names the desk's configured
   harness and model), and every recorded answer carries that briefing's required
   `config_fingerprint`: if the desk's harness, model, mandate or caps changed since, an open or
   no-trade answer is journaled `refused` `DESK_CONFIG_CHANGED` and not applied, while a close is
   applied (it only reduces risk). A recorded `material_fingerprint` is kept only if the
   answered briefing, rebuilt at record time with its answered history, still carries the
   answered fingerprint, so an answer that missed an event journaled while it thought never
   anchors a skip (only a `no_trade` is verified; an unreadable history leaves the answer
   unanchored rather than unrecorded). A live desk takes no per-wake harness or model flag, nor
   a `--command` when its harness is built in (judged against the answered briefing, journaled
   as an `error` wake so the failing alert sees it, and refused by `schedule` too) (a custom harness runs by `--command` under the
   desk's own name), and the API refuses an open or no_trade answer naming another harness or
   model on a live desk (`DESK_LIVE_IDENTITY_MISMATCH`). A failed skip request falls back to
   asking the model. Status changes join `changes` in the same write.
   Any failure to set up or run the harness is journaled as an `error` wake, and a crashed open
   that a read refunds is journaled `refused` `DESK_OPEN_REFUNDED`. The material fingerprint also
   covers `due_since`, so a resume between two wakes wakes the model.
3. **Desk updates.** `PATCH /api/v1/desks/{id}` and `thytrader-desk update` change the harness,
   model, cadence, mandate, caps (merged) or name under a new revision (optional
   `expected_revision`), validated like a new desk. The change (old and new values; a mandate by
   digest) is appended to the desk's `changes` (newest 20) in the same write, so it cannot be lost
   apart from the change; a `desk_updated` audit event is also written, best effort. Mode, quote,
   pool, universe and timeframe stay fixed. A live desk's harness, model, mandate or caps need the
   live acknowledgement (ADR 0148); its name and cadence do not.
4. **Desk wake alerts** (ADR 0115). In the cycle's timed `fleet_supervision` phase, within a
   10-second budget, the execution worker reads the desks and their newest wakes:
   `DESK_WAKES_FAILING` when the newest 3 wakes failed (`error` or `invalid`) or were refused for
   a configuration or identity mismatch (which repeats until an operator acts), naming the failure
   codes only (never harness output, which can carry anything), and `DESK_WAKES_STALLED` when no
   wake (skips count) was journaled for 3 cadences plus 10 minutes since the newest wake or since
   the desk became due to wake (`due_since`: creation, resume, new cadence; never bookkeeping
   writes). Scope `desk`, subject `desk:<desk_id>`; critical for a live desk, warning for paper. A
   paused or retired desk, or a desk no longer listed, resolves its alerts; an unreadable wake
   list or a timed-out pass proves nothing. The alerts only record and warn.

Rejected: comparing the plain fingerprint (it covers recent wakes, so it changes every wake);
skipping while a book is held (its mark, stop or fill can change between bars); counting a live
desk with no open book as live risk for alert-delivery health (it holds no risk; its books count
once open); cross-checking a recorded material fingerprint against the briefings issued (a skip
still needs the live picture to match it, and record keeps a recorded fingerprint only when it
matches the desk at record time); a monotonic journal sequence column (the API is one process and
mints strictly increasing wake ids under the desk's lock, so no migration is needed); an index on
`(desk_id, wake_id)` (desk journals are small, and the worker pass is bounded).

Ops contract v111.
