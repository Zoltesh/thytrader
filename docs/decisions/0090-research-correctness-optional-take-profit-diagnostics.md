# 0090: Optional take-profit, named entry skips, backtest diagnostics, account-rate fees, HTTP signal trace

- Status: Accepted — amended by [0093](0093-signal-based-exits.md)
- Date: 2026-10-02
- Amends: [0036](0036-phase-13-live-extras.md) (live protection is an OCO **or**, without a
  take-profit, a venue stop-limit), [0045](0045-spot-shorting-and-attached-entry-brackets.md)
  (an entry without a take-profit never attaches a bracket), [0008](0008-deterministic-signal-evaluation.md)
  (`thytrader-research-evaluate` is HTTP-backed), and the fee-tier research defaults of
  the 2026-09-13 fee-tier research-defaults plan (removed; see git history)
- Relates to: [0005](0005-canonical-strategy-schema.md), [0019](0019-ops-contract-identity.md),
  [0030](0030-agent-e2e-primary-surface.md), [0048](0048-paper-deploy-fee-fields.md),
  [0058](0058-protection-lifecycle-accounting.md), [0083](0083-unified-backtest-model.md),
  [0087](0087-per-bar-decision-timeline.md)

## Context

Live research on 2026-10-02 found four defects.

1. **Silent short skip.** Entry geometry is `target = entry − stop_distance × reward_multiple` for
   shorts. When that is at or below zero (a 10R target on a 3 ATR stop needs only ~3.3% ATR) the
   backtest kernel and the shared paper/live sizer (`execution/sizing.py`) returned `None` and the
   entry simply never happened. AVAX-USDC 1d, EMA(20) crosses below EMA(100), 3 ATR stop: 10R →
   0 trades in five years, 5R → 2, 3R → 5 — the same signal on the same data — with no diagnostic
   anywhere. Paper/live journaled the bar as `ENTRY_BLOCKED / SIZING_UNAVAILABLE`, which named
   neither geometry nor the cause. Trend strategies also had no way to say "no target, exit on the
   stop, trail, or time".
2. **No backtest diagnostics.** A zero-trade result could not explain itself: nothing counted
   matched signals, rested/expired/repriced entries, or why a matched signal rested nothing.
3. **Fee suggestions understated costs.** `GET /api/v1/fees` suggested the pinned public schedule
   band (0.40% maker / 0.60% taker for $0–10k volume) although the same response reported the
   account's Coinbase rates (0.5% / 0.9% on the Intro tier, $24 30-day volume) and live fills
   confirmed them. Research and paper prefills, and the skills, copied the cheaper numbers.
4. **`thytrader-research-evaluate` failed for every run.** It read PostgreSQL and the Parquet
   dataset root directly from the operator's shell. The evaluator itself already used the unified
   `thytrader-backtest` contract (a direct run against a reachable database and dataset root
   succeeds), but Compose keeps the datasets in the API container's volume and the agent CLIs are
   HTTP-first ([ADR 0030](0030-agent-e2e-primary-surface.md)), so it could not run where operators
   are, and a blanket `except Exception` collapsed every cause into "Signal evaluation failed
   safely".

## Decision

### 1. `take_profit` is optional: `{"kind": "none"}`

- `exits.take_profit` is `{"kind": "reward_risk", "multiple": …}` **or** `{"kind": "none"}`. The
  latter is the only canonical form (a stray `multiple` is rejected). `reward_risk` documents keep
  byte-identical canonical JSON and fingerprints; `schema_version` stays `1.0`.
- **Backtest:** no target is computed and no take-profit ever fills; the stop, the optional ATR
  trail, the time exit, and evaluation-end liquidation are unchanged.
- **Paper:** no take-profit order rests. The book stays `open` and the synthetic stop is enforced
  on every closed bar, as before. Protection status reports such a paper book `covered`.
- **Live:** a Coinbase attached `trigger_bracket_gtc` requires a take-profit `limit_price`, and so
  does a post-fill OCO. An entry without a target therefore never attaches a bracket. After the
  fill the worker rests **one venue stop-limit** (`stop_limit_stop_limit_gtc`, intent purpose
  `bracket`, order kind `stop_limit`): `stop_price` = the working stop, `stop_direction`
  `STOP_DIRECTION_STOP_DOWN` for a long's sell (`STOP_UP` for a short's buy-to-cover), and
  `limit_price` 5% through the stop (sell rounded down, buy rounded up to the price increment).
  The 5% offset is the one Coinbase applies to the stop leg of its own TP/SL bracket, so the
  stop-only book has exactly the gap risk of every other live book. Trailing ratchets cancel and
  replace it like an OCO; identical venue rejections back off and latch like brackets
  (`PROTECTIVE_SUBMIT_REJECTED`, label `live stop-limit`); a time exit or flatten cancels it before
  the marketable exit. Paper never submits `stop_limit`.

### 2. No silent skip: one shared geometry and named reasons

- `execution/geometry.entry_levels` is the single stop/target geometry for backtest (exact prices)
  and paper/live (venue increments). Every refusal returns an `EntrySkipReason`:
  `entry_price_not_positive`, `stop_distance_not_positive`, `stop_not_positive`,
  `target_not_positive`, `stop_within_price_increment`, `target_within_price_increment`,
  `sizing_cash_unavailable`, `no_open_position`, `insufficient_cash`, `notional_below_minimum`,
  `quantity_below_venue_minimum`, `notional_below_venue_minimum`.
- The decision journal records a matched signal that sizing or geometry refused as outcome
  `skipped` with `reason_code` = the upper-cased reason (for example `TARGET_NOT_POSITIVE`) and
  `skip_reason` `entry_geometry` or `entry_sizing`. `SIZING_UNAVAILABLE` is retired.
- **Save-time warning, not an error.** Strategy responses (`GET/PUT/POST /api/v1/strategies…`)
  and `thytrader-research` digests carry `validation.warnings[]`
  (`short_target_may_be_non_positive`, `long_stop_may_be_non_positive`) when the non-positive
  ATR/price threshold (`1 / (stop_multiple × reward_multiple)` for shorts, `1 / stop_multiple` for
  longs) is at or below a stressed ceiling of 20% daily ATR scaled by √(bar length / 1 day). The
  Build stage lists them under the saved definition.

### 3. Backtest diagnostics beside, not inside, the result

`thytrader-backtest-diagnostics-v1` counts `signals_matched`, `entries_rested`, `entries_filled`,
`entries_expired`, `entries_repriced`, `entries_refused_at_fill`, `entries_unfilled_at_end`,
`entries_size_capped` (a notional cap clamped the order — "above max" is a clamp, never a skip),
`warmup_bars`, and `skipped[]` (`{reason, count}` for `pending_entry`, `cooldown`,
`max_positions`, `in_position`, and every `EntrySkipReason`). The model validates its funnel:
`signals_matched = entries_rested + Σ skipped` and `entries_rested = filled + expired +
refused_at_fill + unfilled_at_end`. The counters live in the new nullable
`published_backtest_results.diagnostics_json` column (Alembic 0055), **outside** the canonical
result bytes, so every result fingerprint is unchanged (pinned by golden tests). Republishing an
identical result fills diagnostics a pre-0055 row never recorded and never overwrites recorded
ones. They surface on `GET /api/v1/backtests/{fp}` (summary and full), `thytrader-research
show-result`, and the Test stage's "Why so few trades?" disclosure (open by default on a zero-trade
result). Rows from before 0055 report `diagnostics: null`.

### 4. Fee prefills use the account's reported rates

With credentials, `suggested_maker_fee_rate` / `suggested_taker_fee_rate` equal the account's
Coinbase `maker_fee_rate` / `taker_fee_rate` and `suggestion_source` is `coinbase_account`
(`coinbase_fee_schedule` is retired). The pinned public band is context only:
`suggestion_schedule_tier_id`, `suggestion_schedule_version`, `suggestion_schedule_as_of`, and the
new `schedule_maker_fee_rate` / `schedule_taker_fee_rate`. Demo or missing credentials still
suggest nothing. The operator `fees` report matches. The research launch panel and paper start
form prefill the account rates; skills tell agents to copy `suggested_*` for backtests, studies,
and `thytrader-runtime start --mode paper`. The server-side paper default for omitted rates
(0.001 / 0.002, [ADR 0048](0048-paper-deploy-fee-fields.md)) is unchanged.

### 5. `thytrader-research-evaluate` is ported to the HTTP API

`GET /api/v1/backtests/{result_fingerprint}/signal-trace?outcome=all|matched|not_matched|undefined&limit=1..1000&cursor=…`
loads the result, its verified source run and strategy snapshot, re-evaluates the primary
product's trace on the API's read-only dataset volume (off the event loop), and **fails closed
(503 `signal_trace_unavailable`) unless the trace reproduces the result's
`signal_trace_fingerprint`**. It returns outcome `counts`, `total_records`, one bounded page of
`SignalTraceRecord`s, and `next_cursor`. The CLI takes a `result_fingerprint` (or a
`run_fingerprint`, resolved to its newest result), checks the ops contract, and prints the API's
redacted reason on failure instead of a generic message. It still creates nothing. We chose to
port rather than remove it: the per-bar trace (indicator values and outcome on every bar) is the
only research-side answer to "did my rule fire on that bar?", paper/live already have the
equivalent decision timeline, and the HTTP route removes the direct-database read that made the
CLI unusable from `ops/`.

### Contract

Ops contract `thytrader-ops-contract-v50`, expected Alembic revision `0055`, new fields
`take_profit_kinds` (`reward_risk`, `none`), `live_protection_kinds` (`trigger_bracket`,
`stop_limit`), `backtest_diagnostics` (`thytrader-backtest-diagnostics-v1`), and
`fee_suggestion_source` (`coinbase_account`). Migration 0055 adds `diagnostics_json`, admits
`stop_limit` in `ck_order_intents_kind`, and makes `execution_positions.target_price` nullable;
its downgrade refuses while a `stop_limit` intent or an untargeted position exists.

## Consequences

- A short whose target cannot be positive is visible three ways: a save-time warning, a named
  backtest skip count, and a `skipped / TARGET_NOT_POSITIVE` decision row. Trend shorts can drop
  the target instead of shrinking the reward multiple until trades appear.
- Live books without a take-profit hold a venue stop-limit, never an unprotected position and
  never a fabricated far-away target. A stop-limit can rest unfilled if price gaps through its
  limit; that is the same risk the bracket stop leg already carries and is disclosed in
  the security baseline (now `docs/user/safety.md`).
- Research costs default to what the account actually pays; older results that used schedule
  rates keep their fingerprints (their runs fingerprint the rates they used).
- Diagnostics are explanatory evidence: they do not authenticate a result and a corrupt
  diagnostics column reads as null.

## Alternatives considered

- **Absent or `null` take-profit.** Rejected: an omitted field would silently become "no target"
  for a document that forgot it; `{"kind": "none"}` is explicit and mirrors
  `trailing_stop: {"enabled": false}`.
- **Clamp the short target to one price increment.** Rejected: it invents an exit the strategy did
  not declare and still mislabels the trade.
- **Synthetic live stop on closed bars only.** Rejected: on 1d bars that leaves a live book
  unprotected for a day; the venue stop-limit matches the existing bracket protection.
- **Trigger bracket with an unreachable take-profit limit.** Rejected: a fabricated price that the
  venue may reject or that may someday fill.
- **Diagnostics inside the canonical result (new identity).** Rejected: every re-run of an
  existing spec would change result fingerprints.
- **Remove `thytrader-research-evaluate`.** Rejected in favor of the HTTP port (see 5).
- **Suggest the higher of account and schedule rates.** Rejected: the account's billed rate is the
  truth; the schedule is only context.
