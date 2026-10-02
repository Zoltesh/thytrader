# 0087: Durable per-bar decision timeline for paper and live bots

- Status: Accepted
- Date: 2026-10-02
- Amends: [0054](0054-trade-reason-journals.md) (why-trade review surfaces),
  [0080](0080-per-strategy-workspace-build-test-run-why.md) (the Why stage), and
  [0081](0081-live-chrome-portfolio-bot-detail-trade.md) (bot detail "Why it traded")
- Relates to: [0008](0008-deterministic-signal-evaluation.md), [0019](0019-ops-contract-identity.md),
  [0025](0025-multi-timeframe-htf-filter.md), [0030](0030-agent-e2e-primary-surface.md),
  [0041](0041-paper-live-htf-filter-evaluation.md),
  [0056](0056-multi-instrument-documents-and-pyramiding.md),
  [0058](0058-protection-lifecycle-accounting.md), [0083](0083-unified-backtest-model.md)

## Context

A running bot exposes only its latest completed-bar signal (`last_signal`) and, for bars that
persisted an order intent, a trade reason ([ADR 0054](0054-trade-reason-journals.md)). A live bot
that is not trading is therefore a black box: nothing durable says whether its conditions failed
(and by how much), whether risk refused a matched signal, whether it was cooling down, waiting on a
resting entry, warming up, paused by a data gap or the user-order feed, or replaying bars after
downtime. Bot detail and the strategy Why stage carried an honest note that per-bar history was not
recorded. Operators and agents scraped logs to answer "why didn't it trade?".

The evidence already exists in memory on every closed bar: the execution loop evaluates the entry
rule with the deterministic evaluator ([ADR 0008](0008-deterministic-signal-evaluation.md)),
consults the risk gate, and reads before/after book state. It is discarded after the cycle.

## Decision

**One record per bar.** The execution worker journals one `thytrader-bar-decision-v1` record per
`(deployment_id, product_id, bar_starts_at)` for every paper and live strategy bot, including every
covered product of a multi-instrument document. Writes are upserts, so restart replays rewrite the
same row and never duplicate. Discretionary books are not journaled (they have no rule).

**Outcome enum.** `entry_signal` (the rule matched and an entry intent was created; `action` says
whether the order reached the venue: `order_submitted` or only `intent_created`), `no_signal`
(`CONDITIONS_NOT_MET`, or `HTF_FILTER_NOT_MET` when only the HTF filter failed), `holding`, `exit`
(`exit_reason` `stop`, `trail`, `target`, `time`, or `flatten`), `entry_blocked` (risk or
freshness verdict code and message, or an ATR / sizing / spot-short base / maker-price block),
`skipped` (`skip_reason` `cooldown`, `max_open_positions`, `warmup`, `pending_entry`, `paused`,
`stopped`, `data_gap`, `user_feed_gate`, `catch_up`, `entries_disabled`), and `error`
(fail-closed evaluation error or a raised cycle). `action` is one of `none`, `intent_created`,
`order_submitted`, `order_canceled`, `repriced`, with the linked `intent_id` and order ids.

**Explanation, not recomputation.** The record carries the entry rule as a tree (ALL / ANY / NOT
groups and comparison leaves with label such as `RSI(14)`, operator, both operand values, the
previous values for crossovers, and `true` / `false` / `unknown`), the HTF filter when declared, and
the bar's indicator values as the research `SignalTraceRecord`. `evaluate_latest_entry` now keeps the
merged values it already computed (`LatestEntryEvaluation`); every node result is the evaluator's own
`entry_condition_outcome` on that node with those values. Values are exact Decimal strings. Leaf
labels use the indicator catalog's names and parameter order (`Donchian(20) upper`) and mark another
clock (`RSI(14) [4h]`) and a declared bar lag (`Highest(3, high) (1 bar ago)`,
[ADR 0086](0086-indicator-catalog-expansion-and-offset.md)); the value shown is the lagged one the
runtime compared. The
record also has the risk verdict, close price, a position snapshot, the orders created or changed in
the bar, and the fills applied since the previous decision (so a venue bracket filled between bars
is this bar's `exit`). A server-written one-line `summary` (for example
`No trade: RSI(14) 47.21 needs ≥ 50`) serves the UI, CLI, and agents alike.

**Never blocks or alters trading.** The worker binds a per-bar observation scope (a context
variable) around the unchanged closed-bar call; loop helpers only assign facts a snapshot cannot
show (evaluation values, risk/freshness/breaker verdicts, why no entry was attempted, why a matched
signal sent nothing). The record is built after the call returns or raises (a raised call is
journaled as `error` and re-raised unchanged). Each journal read/write is bounded by a 5-second
timeout; failures are logged and audited as `decision_journal_write_failed` (at most once per bot per
15 minutes) and the cycle continues. No exchange call is made for journaling. Bars already evaluated
(between-bar protection) are not rewritten; a data gap or user-feed gate writes a `skipped` row for
the expected bar once.

**Bounded retention.** The worker prunes at startup and every 6 hours: rows older than 180 days and
rows beyond each bot's newest 20,000 decisions (one per bar and covered product), at most 5,000
rows per pass. Rows cascade with their deployment (so strategy deletion removes paper bots'
journals and keeps kept live books').

**Storage and contract.** Alembic `0053` creates `bar_decisions` (primary key
`deployment_id, product_id, bar_starts_at`; indexes for per-bot and per-strategy newest-first
paging and age pruning; canonical record JSON plus filter columns). Ops contract becomes
`thytrader-ops-contract-v47` with `decision_journals: ["paper", "live"]` and expected schema
revision `0053`.

**Surfaces.** `GET /api/v1/deployments/{id}/decisions` and `GET /api/v1/strategies/{id}/decisions`
(cursor paging newest first, repeated `outcome` filter, optional `product_id` / `deployment_id`;
`storage: "unavailable"` without a database), operator report kind `decisions`
(`thytrader-operator decisions`, `GET /api/v1/operator/decisions`), read-only
`thytrader-runtime decisions`, and the operator-chat tool `runtime_decisions`. Bot detail's "Why it
traded" becomes a Decisions timeline (All / Trades / Blocked / No signal; expandable condition chips,
risk reason, linked orders; trade reasons merged into their bar, never duplicated) with a
"Next evaluation ≈" header, and the strategy Why stage shows the same timeline across the
strategy's bots with a deployment selector. The "per-bar history is not recorded yet" note is gone.

## Consequences

- Operators and agents can answer "why did or didn't this bot trade on bar X?" from skills alone.
- One more durable write per bar per covered product (plus one indexed read for the previous
  decision). The journal is explanatory only; trading never reads it.
- Bars processed while the worker was down are journaled when they are replayed (catch-up bars
  record `skipped` / `catch_up` unless they exit or hold). Bars the worker never processed (for
  example while a book was stopped and flat) have no row; absence is not a decision.
- Trade reasons remain the per-intent record ([ADR 0054](0054-trade-reason-journals.md)); the
  timeline links them by `intent_id`.
- Backtests do not write decision rows; research keeps its own signal trace.

## Alternatives considered

- **Recompute decisions on read from candles:** rejected; it could disagree with what the runtime
  saw (data revisions, gaps, intra-cycle state) and would duplicate evaluator logic.
- **Return extra values from `process_closed_bar`:** rejected; it widens the hot trading path's
  signature and every caller for a diagnostic concern. The context-variable scope keeps trading code
  unchanged when nothing observes it.
- **Write from inside the loop:** rejected; a slow or failing journal could then delay order
  handling. Building after the call, with a timeout, isolates it.
- **Log lines or audit events per bar:** rejected; unbounded, unstructured, and not pageable by
  bot or strategy.
- **Keep everything forever:** rejected; per-bar rows grow without bound on 1m bots. The newest
  20,000 decisions or 180 days covers about two weeks of a single-product 1m bot and the full
  180 days of a single-product 1h bot.
