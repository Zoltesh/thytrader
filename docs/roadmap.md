# Delivery Roadmap

This roadmap sequences capabilities and safety gates. It is not a promise of dates. Each phase should produce a usable, tested vertical increment rather than a collection of disconnected scaffolds.

Product destination (Coinbase-first research + trading platform; agent E2E as the primary surface)
is [product vision](product/vision.md), [ADR 0030](decisions/0030-agent-e2e-primary-surface.md), and
[ADR 0031](decisions/0031-coinbase-first-platform-end-state.md). This file sequences **how** we get
there. Do not treat a shipped narrow clock or catalog as the ceiling.

## Current delivery focus: remaining destination (phases 0–14 shipped)

Phases 0–14 and every sequenced destination slice through
[ADR 0045](decisions/0045-spot-shorting-and-attached-entry-brackets.md) are **shipped**.
The Coinbase spot loop an agent can already drive is real: ingest every currently listed venue TF
(`1m`–`1d`), research including WFO/sweeps, on-demand long/short with SL/TP, paper/live, HTF and
per-indicator clocks, YOLO live skip-confirm (live still needs `--i-understand-live`),
journals/notify hooks, and spot shorts with attached brackets
([ADR 0046](decisions/0046-shipped-vs-remaining-0031-destination.md)).
Fail-closed experiential training V1 is shipped
([ADR 0049](decisions/0049-experiential-train-v1.md)).

Vision destination is **not** fully met. Do **not** treat shipped clocks, on-demand, or Phases 7–14
as open work. Thy Builder should take the next item from
[Destination capabilities](#destination-capabilities-accepted-not-current-builder-order) — not
re-implement a shipped phase. Extra exchanges wait on an explicit yes. Detail:
[agent-driven platform gap plan](plans/2026-09-12-agent-driven-platform-gap-plan.md)
and [fee-tier research defaults](plans/2026-09-13-fee-tier-research-defaults.md).

Completed capability checklist (Phases 0–6):

1. **Create and research in the browser:** ✅ author, publish, backtest, inspect evidence.
2. **Observe through supported agent interfaces:** ✅ `thytrader-operator` CLI/API and skill — no trading authority.
3. **Permit bounded research automation:** ✅ confirmation-gated `thytrader-research` CLI and skill (strategy create/save/import/clone/delete, backtests, and composed studies).
4. **Automate in paper mode:** ✅ 1h and 5m candle-close paper loop, Deploy tab, pause/resume/stop.
5. **Live maker execution:** ✅ Deploy → live places Advanced Trade spot orders when credentials
   exist. Phase 13 live extras (5m live, ATR trailing, user-order WS, native OCO) are also shipped.
6. **Operator/agent integration:** ✅ HTTP-first diagnostics and research CLIs, plus a separate
   confirmation-gated `thytrader-runtime` skill for paper/live control.

Remote / SaaS exposure remains explicitly deprioritized.

## Phase 7: Remaining market-data timeframes — ✅ Shipped

Extend the same durable complete-only Parquet + manifest + verify contract beyond 1h/5m.

### Iterative slices (ship separately)

1. **15m datasets** — ✅ Shipped: worker ingest/publish/verify/catalog + thin diagnostics.
   Same complete-only rules; no candle interpolation. In **this slice**, strategy `timeframe` and
   paper/live clocks stayed `1h`|`5m` (live `1h` only); 15m was not yet a research or execution
   clock. [ADR 0040](decisions/0040-venue-strategy-paper-live-htf-clocks.md) later widened those clocks.
2. **30m datasets** — ✅ Shipped: worker ingest/publish/verify/catalog + thin diagnostics.
   Same complete-only rules; no candle interpolation. In **this slice**, strategy `timeframe` and
   paper/live clocks stayed `1h`|`5m` (live `1h` only); 30m was not yet a research or execution
   clock. [ADR 0040](decisions/0040-venue-strategy-paper-live-htf-clocks.md) later widened those clocks.
3. **6h datasets** — ✅ Shipped: worker ingest/publish/verify/catalog + thin diagnostics.
   Same complete-only rules; no candle interpolation. A complete UTC day is exactly four aligned
   6h candles. In **this slice**, strategy `timeframe` and paper/live clocks stayed `1h`|`5m`
   (live `1h` only); 6h was not yet a research or execution clock.
   [ADR 0040](decisions/0040-venue-strategy-paper-live-htf-clocks.md) later widened those clocks.
4. **1d datasets** — ✅ Shipped: worker ingest/publish/verify/catalog + thin diagnostics.
   Same complete-only rules; no candle interpolation. A complete UTC day is exactly one aligned
   1d candle. In **this slice**, strategy `timeframe` and paper/live clocks stayed `1h`|`5m`
   (live `1h` only); 1d was not yet a research or execution clock.
   [ADR 0040](decisions/0040-venue-strategy-paper-live-htf-clocks.md) later widened those clocks.
5. **Agent data-loop hardening** — ✅ Shipped: `watch_complete` is the completion decision field,
   gap inspection covers the full watch window (or returns `truncated` with a partial `gap_summary`
   when a server-side budget stops the scan; [ADR 0072](decisions/0072-catalog-health-bounded-gaps-self-complete-ingest.md)),
   and every HTTP agent CLI fails closed on an unequal
   or missing ops contract. `complete` remains island completeness.

**Exit gate met:** each timeframe has verified fingerprint-addressed datasets usable as research
inputs once strategy/runtime contracts explicitly allow that TF, and agents can distinguish island
completeness from full watch coverage.

Remaining Coinbase-listed granularities (`1m`, `2h`, and `4h`) now have complete-only datasets
([ADR 0038](decisions/0038-complete-only-1m-2h-4h-datasets.md)). Strategy/paper/live clocks for those
TFs shipped later in [ADR 0040](decisions/0040-venue-strategy-paper-live-htf-clocks.md) and are not
part of the Phase 7 exit.

## Phase 7.1: Fee-tier suggested defaults for research/paper — ✅ Shipped

Prefill Research with maker/taker rates derived from the shipped Coinbase fee-tier snapshot
mapped through versioned schedule `coinbase-advanced-spot-fees-v1`; fields stay editable;
submitted runs fingerprint the rates actually used; honest "suggested vs custom" labels.
Paper deploy and new paper discretionary books persist Decimal `maker_fee_rate` /
`taker_fee_rate` assumptions ([ADR 0048](decisions/0048-paper-deploy-fee-fields.md)).
Omitted paper rates keep the documented `0.001` / `0.002` defaults. Live venue billing is
unchanged. Design:
[fee-tier research defaults](plans/2026-09-13-fee-tier-research-defaults.md).

**Exit gate met:** credentials → suggested rates with override; demo/missing → blank required
research fields or documented paper defaults; submitted runs fingerprint rates; paper books
record the rates used on fills; UI never claims observed Coinbase fills for research/paper
costs.

## Phase 8: Multi-timeframe strategy semantics — ✅ Shipped (research HTF filter)

Optional `htf_filter` + LTF entry ([ADR 0025](decisions/0025-multi-timeframe-htf-filter.md)). Top-level
`timeframe` remains the `1h`|`5m` decision clock in this slice. Research backtests evaluate last-completed
HTF bars and fingerprint both datasets. Paper and live rejected HTF-filter strategies in this slice.
5m live remained Phase 13. [ADR 0040](decisions/0040-venue-strategy-paper-live-htf-clocks.md) later
widened LTF and HTF tokens to every ingested venue clock.
[ADR 0041](decisions/0041-paper-live-htf-filter-evaluation.md) later evaluated those filters in paper
and live. Per-indicator timeframes and mixed-TF crossovers are not in this slice.

## Phase 9: Wider fail-closed indicator catalog — ✅ Five slices shipped

Expand the bounded indicator registry without TA-library passthrough; same deterministic
warmup and no-lookahead rules.

### Iterative slices (ship separately)

1. **Single-output rolling extremes and stdev** — ✅ Shipped ([ADR 0026](decisions/0026-phase-9-single-output-indicator-catalog.md)):
   `highest` (high, period 2–500), `lowest` (low, period 2–500), and population `stdev` (close,
   period 2–500). Same `decimal64-half-even-v1` left-fold, inclusive current bar, insufficient
   warmup → undefined/null (not 0), tri-state conditions. Research, paper, and live share
   the LTF catalog. HTF may declare the same kinds inside `htf_filter`. Paper/live HTF evaluation
   shipped later ([ADR 0041](decisions/0041-paper-live-htf-filter-evaluation.md)). No MACD/Bollinger.
   No per-indicator timeframes. 5m live remained Phase 13 and later shipped.
2. **Momentum and HLC oscillators** — ✅ Shipped ([ADR 0027](decisions/0027-phase-9-roc-williams-cci.md)):
   `roc` (close, period 2–500, warmup `period + 1`), `williams_r` (high/low/close, period 2–100),
   and `cci` (high/low/close, period 2–100, typical price SMA and population MAD, Lambert `0.015`).
   Zero divisors → undefined. Same shared LTF catalog. Still no MACD/Bollinger, identity OHLCV
   series, constant-level series, or per-indicator timeframes.
3. **Identity OHLCV and constant levels** — ✅ Shipped ([ADR 0028](decisions/0028-phase-9-identity-constant.md)):
   `identity` (one of open/high/low/close/volume, empty parameters, warmup 1) and `constant`
   (`parameters.value`, no input, warmup 1). Crossovers still require two indicator operands.
   Same shared LTF catalog. Still no MACD/Bollinger, configurable rolling inputs, sample stdev,
   or per-indicator timeframes.
4. **Weighted average, momentum, and MFI** — ✅ Shipped ([ADR 0029](decisions/0029-phase-9-wma-momentum-mfi.md)):
   `wma` (close, period 2–500, oldest weight 1 / newest weight `period`), `momentum` (close, period
   2–500, warmup `period + 1`, `close - close[period]`), and `mfi` (high/low/close/volume, period
   2–100, warmup `period + 1`, `100 * positive / (positive + negative)`). Zero money-flow total →
   undefined. Same shared LTF catalog. Still no MACD/Bollinger, configurable rolling inputs, sample
   stdev, or per-indicator timeframes.
5. **Multi-series outputs (MACD, Bollinger)** — ✅ Shipped ([ADR 0032](decisions/0032-phase-9-macd-bollinger.md)):
   `macd` (close; `fast_period`/`slow_period`/`signal_period` each 2–500, fast < slow; series
   `macd`/`signal`/`histogram`) and `bollinger` (close; `period` 2–500 and `stdev_multiplier` `> 0`
   and `≤ 10`; series `middle`/`upper`/`lower`). Conditions reference a series id on these kinds and
   omit `series` on single-output kinds. Same shipped EMA/SMA/population-stdev arithmetic. Same
   shared LTF catalog. Still no stochastic, ADX, configurable rolling inputs, sample stdev, or
   per-indicator timeframes.
6. **Per-indicator timeframes** — 📋 Out of Phase 9 (ADR 0025). Shipped later as
   [ADR 0042](decisions/0042-per-indicator-timeframes.md).

**This-slice exit gate met:** the two kinds and the series-id contract are named in the ADR,
implemented in the registry and evaluator, referenced from conditions/crossovers, and listed
honestly in the operator catalog and the then-current engine matrix.

## Phase 10: Portfolio + risk-policy registry — ✅ Shipped

Typed `thytrader-risk-policy-v1` registry with capital allocation and concurrent single-instrument
paper/live under one policy ([ADR 0033](decisions/0033-phase-10-risk-policy-registry.md)). Strategy
documents stay `max_concurrent_positions = 1`; multi-asset here means concurrent deployments of
those documents, not a multi-instrument strategy schema. Compiled default: empty allowlist and
allocations, eight running slots and eight open positions per mode, unit exposure fractions, and
`paper_capital_quote` `100000`. Operator `risk` reports `risk_policy_registry: available`. Entries
are gated before intent persist; exits are not. Denied entries skip the bar.

**Exit gate met:** two published single-instrument strategies can run paper together when the
policy has spare slots, capital, and allowlist room; operator risk is available; runtime
`set-risk-policy --confirm` publishes an immutable version; ops contract is
`thytrader-ops-contract-v7` / Alembic `0021`.

This slice did not include on-demand trades, `1m`/`2h` clocks, journals, or notify; those shipped
later ([ADR 0039](decisions/0039-on-demand-discretionary-trades.md),
[ADR 0040](decisions/0040-venue-strategy-paper-live-htf-clocks.md),
[ADR 0037](decisions/0037-phase-14-experiential-memory.md),
[ADR 0046](decisions/0046-shipped-vs-remaining-0031-destination.md)). Intra-strategy pyramiding,
multi-instrument strategy documents, and daily-loss/drawdown circuit breakers remain destination.

## Phase 11: Research rigor tooling — ✅ Shipped

Walk-forward / out-of-sample / cross-market studies compose the then-current bar-backtest engines
([ADR 0035](decisions/0035-phase-11-research-rigor.md)). `ema-trend` remains the default draft
template; `rsi-mean-reversion`, `macd-trend`, and `bollinger-mean-reversion` are additional starting
drafts. The Build inspector listed per-engine support. Walk-forward is **validation**, not
parameter optimization. Child backtests remain the append-only evidence; there is no Alembic
revision. Cross-market still requires one published single-instrument strategy per product.

**Exit gate met:** agents can `plan-study` / `submit-study --confirm` for OOS holdout, rolling or
anchored walk-forward, and 2–8 product cross-market studies; the UI can launch OOS and walk-forward
on a published fingerprint; templates are selectable; the engine matrix was honest about support.

Parameter sweeps, walk-forward optimization, and stitched OOS equity shipped later as
[ADR 0044](decisions/0044-parameter-sweeps-wfo-stitched-equity.md). Paper/live HTF is
[ADR 0041](decisions/0041-paper-live-htf-filter-evaluation.md).

## Parameter sweeps / WFO / stitched OOS equity — ✅ Shipped

Research composition can `plan-study` / `submit-study --confirm` for `parameter_sweep` and
`walk_forward_optimization` without inventing grid math or looking ahead from OOS into selection
([ADR 0044](decisions/0044-parameter-sweeps-wfo-stitched-equity.md)). Child evidence stays ordinary backtest results.
Derived axis candidates publish on submit through the existing store. Stitched OOS equity compounds
non-overlapping window **returns**; overlapping OOS and embargo gaps are not interpolated.
Walk-forward **validation** is unchanged. YOLO and playbook are unchanged by this slice.

**Exit gate met:** agents can plan and submit sweeps (published fingerprints or parameter axes) and
WFO; selected OOS is the WFO claim; stitched equity is derived when geometry allows; complete-only
datasets; no ops-contract bump.

## Phase 12: Agent orchestration + YOLO opt-in — ✅ Shipped

Playbook skill over existing CLIs so an agent can sequence data → research → optional paper without
inventing a private workflow. Default remains `--confirm`. YOLO mode (default off) skips confirmation
on allowed tiers `data`, `research`, `paper`, and/or `live` after an audited skip.
`--i-understand-live` remains required for live money. See [agent integration](agent-integration.md),
[ADR 0034](decisions/0034-phase-12-agent-orchestration-yolo.md), and
[ADR 0043](decisions/0043-yolo-live-skip-confirm.md).
This phase serves [ADR 0030](decisions/0030-agent-e2e-primary-surface.md) (agent E2E as primary
surface); it does not collapse skill lanes or grant live authority by inheritance.

**Exit gate met:** `uv run thytrader-playbook status` advertises Safe vs YOLO; `run` calls existing
lane CLIs and never starts live; `--confirm` remains the default; live start still needs
`--i-understand-live`; skipped confirms audit `confirm_skipped` or fail closed. Live YOLO
`--confirm` skips shipped later ([ADR 0043](decisions/0043-yolo-live-skip-confirm.md)).

This slice did not include on-demand trades, `1m`/`2h` clocks, journals, or notify; those shipped
later ([ADR 0039](decisions/0039-on-demand-discretionary-trades.md),
[ADR 0040](decisions/0040-venue-strategy-paper-live-htf-clocks.md),
[ADR 0037](decisions/0037-phase-14-experiential-memory.md),
[ADR 0046](decisions/0046-shipped-vs-remaining-0031-destination.md)).

## Phase 13: Live extras — ✅ Shipped

5m live (same closed-bar clock as paper), ATR trailing stops, authenticated user-order WebSockets,
and native Coinbase `trigger_bracket_gtc` OCO after live entry fills ([ADR 0036](decisions/0036-phase-13-live-extras.md)).
Paper still simulates OCO with a post-only take-profit plus a synthetic stop. HTF-filter publications
were still rejected in this slice; paper/live HTF evaluation shipped later
([ADR 0041](decisions/0041-paper-live-htf-filter-evaluation.md)). Daily-loss / drawdown breakers stay
destination.

On-demand/discretionary trades with SL/TP were out of this live-extras slice; they shipped later
([ADR 0039](decisions/0039-on-demand-discretionary-trades.md),
[ADR 0045](decisions/0045-spot-shorting-and-attached-entry-brackets.md),
[ADR 0046](decisions/0046-shipped-vs-remaining-0031-destination.md)). Do not treat them as a side
effect of 5m live.

**Exit gate met:** a published 5m strategy can arm live when credentials exist; live exits after fill
are one venue OCO; trailing is durable; operator `runtime` reports user-order feed state; 5m live
pauses when that feed is not connected; ops contract is `thytrader-ops-contract-v8` / Alembic `0022`.

## Phase 14: Experiential memory / hindsight — ✅ Shipped

Operator-managed facts and lessons so agents can improve from durable, origin-attributed evidence
across research, backtests, paper, and live — without substituting for audit trails
([ADR 0037](decisions/0037-phase-14-experiential-memory.md)). Schema
`thytrader-experiential-memory-v1` journals (`fact` / `lesson` / `note`), sentiment snapshots, and
pattern-learning hooks require `origin` `human` or `agent`. Monitor is `thytrader-monitor-v1`.
Notify providers are `none` (default), `log`, and `webhook`. YOLO never covers this lane.
Ops contract is `thytrader-ops-contract-v9` / Alembic `0023`.

**Exit gate met:** `uv run thytrader-memory` can status/monitor/list and, with `--confirm`, append
journals, sentiment, pattern hooks, and notify requests; operator `monitor` is read-only; webhook
URLs are redacted; default notify sends nothing.

No venue scrape and no fill-ledger origin rewrite. Model training is a follow-on
([ADR 0049](decisions/0049-experiential-train-v1.md)). Phase 13 live extras,
on-demand SL/TP, and `1m`/`2h` clocks were out of this memory slice; they shipped in other ADRs.

## On-demand discretionary trades with SL/TP — ✅ Shipped

Long-only on-demand entries go through the existing order-intent → risk → broker path
([ADR 0039](decisions/0039-on-demand-discretionary-trades.md)). A discretionary book is a `deployments` row with `kind = discretionary` (nullable strategy
identity, stored `1h` or `5m` clock in this slice;
[ADR 0040](decisions/0040-venue-strategy-paper-live-htf-clocks.md) later widened those clocks).
Stop and take-profit are required. Live rests one `trigger_bracket_gtc` after fill; paper
uses synthetic SL/TP. Idempotent retries never call `place_order` again. Timeouts persist
`unknown` and GET-order reconcile. Allocations nonempty deny discretionary. Strategy/paper/live
clocks stayed `1h`/`5m` in this slice. Ops contract is `thytrader-ops-contract-v11` / Alembic `0025`.

**Exit gate met:** `POST /api/v1/discretionary-orders` and `thytrader-runtime place-order --confirm`
(live also `--i-understand-live`) place a long; the Trade UI uses the same HTTP contract with
human origin; operator summaries include `kind`.

Shorting, attached entry brackets, extra venue execution clocks, and YOLO-without-confirm for live
were out of this on-demand slice; they shipped later
([ADR 0045](decisions/0045-spot-shorting-and-attached-entry-brackets.md),
[ADR 0040](decisions/0040-venue-strategy-paper-live-htf-clocks.md),
[ADR 0043](decisions/0043-yolo-live-skip-confirm.md)). Intra-strategy pyramiding remains destination.

## Venue strategy, paper, live, and HTF clocks — ✅ Shipped

Every ingested complete-only venue granularity is a legal strategy LTF, paper clock, live clock,
discretionary book clock, and research HTF token
([ADR 0040](decisions/0040-venue-strategy-paper-live-htf-clocks.md)). Sub-hour live pauses unless
the authenticated user-order feed is connected. Paper/live HTF evaluation stayed out of this clock
slice. No interpolation. Ops contract is `thytrader-ops-contract-v12` / Alembic `0026`.

**Exit gate met:** a published `1m`, `15m`, `30m`, `2h`, `4h`, `6h`, or `1d` strategy can research,
paper, and arm live against a matching complete-only dataset the same way `1h`/`5m` already could;
discretionary books accept those clocks without changing intent persistence, risk, OCO, or
reconcile-before-retry.

Paper/live HTF evaluation, per-indicator timeframes, shorting, and YOLO-without-confirm for live
were out of this clock slice; they shipped later
([ADR 0041](decisions/0041-paper-live-htf-filter-evaluation.md),
[ADR 0042](decisions/0042-per-indicator-timeframes.md),
[ADR 0045](decisions/0045-spot-shorting-and-attached-entry-brackets.md),
[ADR 0043](decisions/0043-yolo-live-skip-confirm.md)). Extra exchanges stay waiting on an explicit
yes.

## Paper and live HTF-filter evaluation — ✅ Shipped

Paper and live evaluate published `htf_filter` with the same last-completed closed-bar AND as
research ([ADR 0041](decisions/0041-paper-live-htf-filter-evaluation.md)). Complete-only HTF candles;
no interpolation; missing HTF coverage pauses. Ops contract is `thytrader-ops-contract-v13` /
Alembic `0026` (no new migration).

**Exit gate met:** a published HTF-filter strategy can start paper and live; the worker ANDs HTF
`when` with LTF entry on last-completed HTF bars; in-progress HTF bars never participate.

## Per-indicator timeframes — ✅ Shipped

LTF-list indicators may declare an optional coarser integer-multiple `timeframe`
([ADR 0042](decisions/0042-per-indicator-timeframes.md)). Last-completed extra-TF bars overlay the
decision-clock row before `entry.when`; `htf_filter` stays a tri-state AND. Research fingerprints
unbound extra TFs as `indicator_dataset_fingerprints`. Paper and live load complete-only extra-TF
windows composed with the HTF path; gaps pause. Ops contract is `thytrader-ops-contract-v14` /
Alembic `0026` (no new migration). Extra exchanges, shorting, and YOLO-without-confirm for live stay
out of this slice.

**Exit gate met:** a 5m strategy can declare a 1h EMA on the LTF list; research, paper, and live
hold last-completed extra-TF values onto each LTF close without interpolation.

## YOLO skip-confirm for live — ✅ Shipped

Operator-enabled YOLO tier `live` skips `--confirm` on live start, pause, resume, and stop after
an audited `confirm_skipped` event ([ADR 0043](decisions/0043-yolo-live-skip-confirm.md)).
`--i-understand-live` remains required for live start and live place-order. YOLO off, a missing
`live` tier, or unavailable audit storage fail closed. Paper YOLO does not cover live. Playbook
never starts live. Live `place-order`, `set-risk-policy`, `--local` research, memory, kill
switches, and venue-order cancellation stay outside this skip. Default remains `--confirm`.
Ops contract is unchanged.

**Exit gate met:** `thytrader-runtime start --mode live --i-understand-live` (no `--confirm`)
succeeds only when YOLO advertises `live` and the skip audit writes; Safe mode still requires
`--confirm`; playbook `run` never constructs `--mode live`.

## Spot shorting and attached entry brackets — ✅ Shipped

Strategy `entry.side` is `long` or `short`. Discretionary HTTP/CLI accept `--side` (default
`long`). Geometry: longs `stop < entry < take_profit`; shorts invert. Paper and backtests
simulate a cash-and-inventory spot short. Live submits Coinbase Advanced Trade **SPOT** `SELL` and
fails closed without available base (`INSUFFICIENT_BASE_FOR_SPOT_SHORT`). Never `leverage`,
`margin_type`, or futures.

When SL/TP are known at persist time and ATR trailing is disabled, live attaches
`attached_order_configuration.trigger_bracket_gtc` (size omitted) and does not rest a second
post-fill OCO. Trailing keeps ADR 0036 post-fill OCO. Paper never sends venue brackets.
Ops contract is `thytrader-ops-contract-v15` / Alembic `0027`. Live YOLO skip-confirm
([ADR 0043](decisions/0043-yolo-live-skip-confirm.md)) and WFO
([ADR 0044](decisions/0044-parameter-sweeps-wfo-stitched-equity.md)) are unchanged.

**Exit gate met:** paper short + attached live entry + fail-closed live short without base; long
golden backtest fingerprints unchanged.

## Wider fail-closed indicator catalog — ✅ Shipped

Stochastic `%K`/`%D`, Wilder ADX / `+DI` / `-DI`, configurable rolling OHLCV inputs, and sample
stdev join the fail-closed registry ([ADR 0047](decisions/0047-wider-fail-closed-indicator-catalog.md)).
Same complete-only candles, last-completed per-indicator clocks, and snapshotted strategy semantics in
backtest, paper, and live. No TA-library passthrough. No interpolated candles. Population `stdev`
and Bollinger bands are unchanged.

**Exit gate met:** kinds named in the ADR, implemented in the registry and evaluator, referenced from
conditions, and listed in operator `indicators` plus the then-current engine matrix.

## Indicator catalog expansion and bar lag — ✅ Shipped

Thirty-two more fail-closed kinds join the registry
([ADR 0086](decisions/0086-indicator-catalog-expansion-and-offset.md)): trend (DEMA, TEMA, Hull,
KAMA, VWMA, Supertrend, Parabolic SAR, Aroon, Ichimoku without displacement, Vortex, linear
regression, TRIX), momentum (stochastic RSI, PPO, Ultimate and Awesome oscillators, CMO, TSI),
volatility (Keltner, Donchian, Bollinger %B and bandwidth, NATR, Choppiness, historical
volatility), volume (OBV, Chaikin Money Flow, A/D line, rolling VWAP, Force Index), and
statistical (z-score, percent rank). Any declaration may lag by `offset` bars. One Python registry
renders the operator `indicators` report and the builder catalog; four templates use the new kinds.

**Exit gate met:** every kind is validated, implemented in the shared calculator, cross-checked
against TA-Lib or an independent Decimal reference, documented with its warmup and edge cases,
and usable in research, paper, live, HTF filters, extra indicator clocks, sweeps, and the builder.

## Paper deploy fee fields — ✅ Shipped

Paper strategy deploy and new paper discretionary books persist Decimal maker/taker assumptions
([ADR 0048](decisions/0048-paper-deploy-fee-fields.md)). CLI `--maker-fee-rate` / `--taker-fee-rate`
and HTTP `maker_fee_rate` / `taker_fee_rate` are optional together; omitted paper uses documented
`0.001` / `0.002`. Live rejects the fields and keeps venue-recorded fees. Ops contract is
`thytrader-ops-contract-v16` / Alembic `0028`. Extra exchanges stay out.

**Exit gate met:** paper books record the chosen rates on fills; operator `fee_treatment` names
those assumptions; copy never claims observed Coinbase fees.

## Bounded experiential training V1 — ✅ Shipped

A fail-closed integer ranker trains from origin-attributed local journals, sentiment, and pattern
hooks ([ADR 0049](decisions/0049-experiential-train-v1.md)). Engine
`thytrader-experiential-train-v1`. Documents `thytrader-experiential-model-v1` and
`thytrader-experiential-advisory-v1`. Evidence is local only (backtest / research / market_data /
deployment / paper_fill / live_fill); dangling pointers fail the train; missing candles are never
interpolated. Output is advisory research input. `create-strategy --experiential-model-id` is HTTP-only
and merges that advisory into JSON. YOLO never covers `train`. Journal schema and review UI stay
with the sibling why-trade slice; this trainer consumes `JournalEntry` as stored. Extra exchanges
stay out. Ops contract is `thytrader-ops-contract-v17` / Alembic `0029`.

**Exit gate met:** `uv run thytrader-memory train --origin agent --confirm` writes a fingerprintable
model; `list-models` / `show-model` read it; research can attach the advisory without changing
strategy semantics or placing orders.

## Daily-loss, drawdown, rate limits, and collars — ✅ Shipped

The Phase 10 registry now gates risk-increasing **entries** (not exits) with UTC-day daily-loss
and per-strategy fill-ledger drawdown breakers, rolling 60-second order/cancel caps, and a
last-close reference-price collar ([ADR 0050](decisions/0050-daily-loss-drawdown-rate-collars.md)).
Daily-loss pauses every running book in that paper or live mode; drawdown pauses this book; rate
and collar deny without pausing. Missing marks fail closed (`BREAKER_MARK_MISSING`) without
pausing. Compiled defaults stay a permissive envelope (`"1"` / `60` / `"0.5"`). Schema stays
`thytrader-risk-policy-v1`; omitted stored keys overlay those defaults. Alembic `0030`
revises trainer `0029` with an overlay comment and does not rewrite stored policy JSON.
Ops contract is `thytrader-ops-contract-v18` / Alembic `0030`. `--confirm` and
`--i-understand-live` are unchanged. Extra exchanges stay out.

**Exit gate met:** drawdown pause after a stop; collar skip without pause; daily-loss deny in the
entry gate; operator `risk` reports fractions/ints and `DAILY_LOSS_LIMIT` /
`STRATEGY_DRAWDOWN_LIMIT`.

## Trade-reason journals — ✅ Shipped

A human or agent can open a paper or live intent and see **why it was made**: strategy snapshot
(fingerprint and name), closed-bar signal facts actually used, the risk-registry verdict, an optional
discretionary note, and fill/reconcile facts joined from the execution ledger
([ADR 0054](decisions/0054-trade-reason-journals.md)). Schema `thytrader-trade-reason-v1`. One row
per persisted order intent. Denied risk with no intent is not recorded. Recording fails open.
Place-order `--note` is the first note; later notes use
`thytrader-memory add-trade-reason-note --confirm`. YOLO never covers that mutation. The ADR 0049
trainer still consumes `JournalEntry` as stored; this slice owns why-trade review. Same payload
for Memory/Trade review, `GET /api/v1/memory/trade-reasons`, and operator `trade-reasons`. No
interpolated candles. Extra exchanges stay out. Ops contract is `thytrader-ops-contract-v20` /
Alembic `0032`.

**Exit gate met:** a strategy entry and a discretionary place-order with `--note` both list on
`uv run thytrader-memory list-trade-reasons` and `uv run thytrader-operator trade-reasons`; a later
`--confirm` note appends without rewriting fills.

## Per-bar decision timeline — ✅ Shipped

Every paper and live strategy bot journals what it decided on each completed bar and why
([ADR 0087](decisions/0087-per-bar-decision-timeline.md)): one `thytrader-bar-decision-v1` row per
`(deployment_id, product_id, bar_starts_at)` (upserted, so restart replays never duplicate) with the
outcome (`entry_signal`, `no_signal`, `holding`, `exit`, `entry_blocked`, `skipped`, `error`), the
action and linked intent/orders/fills, the entry-rule tree with each leaf's values versus thresholds
and the HTF filter (reusing the research `SignalTraceRecord`), the risk verdict, close price, and
end-of-bar position. Journaling never blocks or alters trading and makes no exchange call; the
worker keeps the newest 20,000 decisions per bot (one per bar and covered product) for at most 180 days. Surfaces:
`GET /api/v1/deployments/{id}/decisions`, `GET /api/v1/strategies/{id}/decisions`, operator report
kind `decisions`, read-only `thytrader-runtime decisions`, the chat tool `runtime_decisions`, the
bot detail Decisions timeline, and the strategy Why stage. Ops contract
`thytrader-ops-contract-v47` / Alembic `0053`.

**Exit gate met:** each outcome is journaled from a real paper cycle; a failing or hung journal
leaves trading identical and audits `decision_journal_write_failed`; PostgreSQL upserts are
idempotent and retention is bounded.

## Agent research ergonomics — ✅ Shipped

Agents researching across many markets no longer hand-bind dataset fingerprints or clone one
strategy per market ([ADR 0089](decisions/0089-agent-research-ergonomics.md)). Backtest and study
starts may omit any dataset fingerprint: the server binds the newest complete catalog dataset per
product and clock (decision, HTF, extra indicator clocks, additional instruments) from the
configured ingestion provider, echoes every binding as `bound_datasets`, and fails closed with 422
`datasets_missing` naming the `watch-add` / `ingest` commands. Studies may omit both evaluation
bounds to use the common covered window. Cross-market studies accept one `strategy_id` plus
`markets[].product_id` and record exact per-market variant snapshots under that strategy. Sweeps and
WFO allow 64 candidates and 512 child windows as async jobs (planned before queuing) while
synchronous submits stay at 8 and 128, with data-snooping warnings. The operator `products` report
carries increments, minimum sizes, status, and alias; `watch-add` answers an unverifiable product
catalog with a retryable 503; agent CLIs name what failed (HTTP status and API code, timeout,
unreachable origin, dropped connection, unreadable input) instead of "failed safely"; docs and
skills resolve the API base URL from settings. Ops contract `thytrader-ops-contract-v49`.

**Exit gate met:** a backtest and an OOS study with no fingerprints or bounds bind and echo the
newest datasets; a cross-market study from one strategy plans three exact variants; a 9-candidate
sweep queues async and is refused synchronously; `plan-study` prints a 422's code and message.
## Research correctness: optional take-profit, named skips, diagnostics — ✅ Shipped

Live research found a short whose take-profit would be at or below zero silently never entering
([ADR 0090](decisions/0090-research-correctness-optional-take-profit-diagnostics.md)). Shipped:
`exits.take_profit: {"kind": "none"}` in backtest, paper (no TP order; synthetic stop), and live
(one Coinbase `stop_limit_stop_limit_gtc` 5% through the stop instead of a TP/SL bracket); one
shared stop/target geometry that names every refusal (`target_not_positive`, `stop_not_positive`,
`notional_below_minimum`, …) in backtest diagnostics and as `skipped` decision rows; advisory
save-time `validation.warnings` for geometry that plausible volatility makes untradable; a
`thytrader-backtest-diagnostics-v1` entry funnel stored beside each result (fingerprints
unchanged) on `show-result`, the API, and the Test stage's "Why so few trades?" disclosure;
fee prefills from the account's reported Coinbase rates with the public schedule as context; and
`thytrader-research-evaluate` ported to `GET /api/v1/backtests/{fp}/signal-trace`. Ops contract
`thytrader-ops-contract-v50` / Alembic `0055`.

**Exit gate met:** the AVAX-style 10R short reports `target_not_positive` instead of zero
unexplained trades; backtest/paper/live parity tests cover `take_profit: none`; golden
fingerprints for pre-existing strategies and results are unchanged.

## Portfolio deployment, portfolio limits, and manager proposals — ✅ Shipped

A portfolio (ADR 0088) now runs
([ADR 0091](decisions/0091-portfolio-deployment-limits-and-manager-proposals.md)). Start plans
every sleeve first and then starts or attaches one bot per sleeve tagged `portfolio_id` (paper
cash or live allocated capital = weight × capital; live needs `i_understand_live`); pause, resume,
and stop (managed or flatten) work per portfolio or per sleeve. A live portfolio's sleeve
allocations count as risk-policy allocation membership for its own bots (approach (a)); standalone
bots are unchanged. The worker supervises each deployed portfolio every cycle: allocations follow
the current weights, new entries must fit the total and per-asset exposure caps
(`PORTFOLIO_TOTAL_EXPOSURE_LIMIT`, `PORTFOLIO_ASSET_EXPOSURE_LIMIT`, fail-closed
`PORTFOLIO_LIMITS_UNAVAILABLE`), and the daily loss and drawdown stops pause every sleeve and
latch until an operator reset. The manager agent runs outside ThyTrader: it reads one briefing
(`thytrader-portfolio-briefing-v1`) and submits proposals (rebalance, pause or resume a sleeve, add
a sleeve; never an order) that auto-apply only inside its permissions and otherwise wait for
Approve / Decline on the Manager tab, with every step journaled. Surfaces: the portfolio
deployment, breaker, proposal, and briefing routes; `thytrader-runtime portfolio-*`;
`thytrader-portfolio` manager commands; the operator `portfolios` report; the Portfolio page.
Ops contract `thytrader-ops-contract-v51` / Alembic `0056`.

**Exit gate met:** paper and live portfolios start, pause, and stop through worker cycles with a
fake broker; an entry over a cap is refused with its reason code in the decision timeline; a
tripped breaker pauses every sleeve, re-pauses a back-door resume, and stays latched until reset;
proposals walk pending → applied / declined / failed / expired with auto-apply bounded by the
permissions and the weekly budget.

## Research worker pool: research leaves the API process — ✅ Shipped

Under research load the API sat at 100% of one core and a 1d backtest round trip took 46 s
([ADR 0092](decisions/0092-research-worker-pool.md)). Shipped: a `research-worker` Compose service
(supervisor plus `THYTRADER_RESEARCH_WORKER_COUNT` worker processes, default 2) that is the only
place backtests, studies, and portfolio backtests run; leased `FOR UPDATE SKIP LOCKED` claims on the
existing job tables with heartbeat renewal, crash and lease-expiry re-queue, an attempt limit, and
lease-fenced writes; process recycling after a job count or RSS growth; an API that only validates,
queues, and long-polls (sync submits answer 201, the same 422/503 via `error_code`, or 202 with the
job); operator health with worker liveness, per-worker RSS, and queue depth;
`thytrader-research list-research-jobs`; the UI polls a 202. Ops contract
`thytrader-ops-contract-v52` / Alembic `0057`.

**Exit gate met:** the API answers health in milliseconds and uses a small fraction of a core
while four heavy backtests run on two workers; a SIGKILLed worker's job is re-queued and completes;
the same request through the in-process harness and the worker returns byte-identical bodies.

## Signal-based exits — ✅ Shipped

Strategies can hold a trend until it reverses
([ADR 0093](decisions/0093-signal-based-exits.md)). An optional `exits.signal_exit` rule tree,
which uses the entry grammar and operands, closes an open position when it matches on a closed bar
after the fill bar, as a taker at that close (the time-exit convention), in backtest, paper, and
live. The mandatory initial stop still guards the position and wins a same-bar tie. The trail,
take-profit, and time exit still apply. Backtests report exit reason `signal`, `exit_reasons`
diagnostics, trace `exit_condition`, and the `signal_exit_at_close` validity limit. Live cancels
protection before the marketable cover and keeps exiting through a pending cancel by using a
durable position marker. Decision rows show `exit_reason: signal` with the evaluated exit rule.
The Build stage has an "Exit when" section, and template `ema-trend-hold` (EMA 20/100 cross in and
out, 3× ATR stop, no take-profit, wide 5× ATR trail) ships for the trend sleeves. Ops contract
`thytrader-ops-contract-v53` / Alembic `0058`.

**Exit gate met:** golden fingerprints of documents without a rule are unchanged. Kernel tests pin
the close-time exit, no exit on the fill bar, stop and take-profit precedence, and signal-before-time.
Paper and fake-broker live tests pin cancel-protection-then-sell, a pending-cancel race that never
re-rests protection, and a sleeve's allocation freed on exit.

## Research honesty and agent ergonomics — ✅ Shipped

A day of agents driving ThyTrader found results that hid their window, study summaries that needed
nine calls to read, two validity shapes, validator-internal issue paths, and bulk chores that cost
dozens of calls ([ADR 0094](decisions/0094-research-honesty-and-agent-ergonomics.md)). Shipped:
an evaluated `window` on every backtest result (HTTP, CLI, operator `performance`, the Test stage;
outside the result bytes); study rows with axis values and bounds, per-candidate OOS sums, and a
thinned stitched OOS path; one `validation` shape plus a stderr line for invalid drafts; document
issue paths with plain messages; JSON-number decimals with unchanged fingerprints; 12-digit
decision operands and honest crossover summaries; `thytrader-portfolio delete`, `create` with
limits and manager settings, `add-sleeves --file` in one revision, and a 32-sleeve cap;
`clone-strategy --name`; one INFO line (not a 404 ERROR per report) for an asset with no USD
market; the library `tag` filter with UI chips and `bulk-delete-strategies --tag`. Ops contract
`thytrader-ops-contract-v54` / Alembic `0058` (no schema change).

**Exit gate met:** one `show-study` call explains a WFO; `5` and `"5"` produce identical request
and execution fingerprints; an invalid import prints its first document-path issue on stderr.

## Sparse markets keep their history — ✅ Shipped

A quiet interval no longer shrinks a dataset to its newest island
([ADR 0095](decisions/0095-sparse-markets-no-trade-bars-listing-floors.md)). A thin market showed the bug: BONK-USD 1m kept two candles of a
90-day watch and reported them complete.

- Confirmed intervals without trades become flat zero-volume bars, counted as
  `synthetic_no_trade_intervals` and disclosed on results as `synthetic_no_trade_bars`. Paper and
  live fill them the same way and name them `no_trade_bar` on decision rows.
- `history_floor_at` comes only from a backward listing search that reaches past the lookback
  ceiling; forward walks never move it.
- Catalog `complete` is watch-relative, with coverage X of Y in the data-catalog report and the Home
  data-health table.
- Alembic `0059` cleared every older floor, and the worker re-proves floors once per process. Ops
  contract `thytrader-ops-contract-v55`.

**Exit gate met:** liquid gap-free series keep byte-identical dataset fingerprints. Worker tests
pin these behaviors: a sparse forward chunk keeps history, a backward walk past the listing records
a floor, a forward gap never moves it, quiet days are skipped with daily probes, a bogus floor is
repaired on the first visit, and catalog completeness is judged against the lookback.

**Shipped follow-up:** ADR 0104 adds a fixed 120-second wait for only a settling newest decision
candle. No entries run during the wait; older gaps and expired waits still pause.

## Reference instruments — ✅ Shipped

Strategies can gate on another market without trading it
([ADR 0096](decisions/0096-reference-instruments.md)), for example "alts only while BTC-USDC 1d
close > EMA(100)".

- `data_requirements.reference_instruments` (at most 3, same quote currency, decision clock or a
  coarser integer multiple) is read by indicators with `source`. Each reference's warmup is derived
  from its indicators, and it uses closed-bar alignment like an HTF filter.
- Research auto-binds reference datasets (`bound_datasets` role `reference`), and cross-market
  studies keep the reference fixed.
- Paper and live load reference bars every cycle. A stale or missing reference skips entries with
  `reference_data_stale` / `reference_data_missing`. A start is refused until each reference
  series is watched.
- The builder has a reference block and an indicator instrument picker, with `BTC · EMA(100)`
  labels. Template `btc-regime-gate`. Ops contract `thytrader-ops-contract-v56`.

**Exit gate met:** reference-free documents keep their canonical bytes, results, and fingerprints
(golden pinned). Tests prove an in-progress reference bar never changes a value, backtests are
deterministic, and the paper/live fake loop fails closed on stale and missing references.

**Deferred:** cross-instrument orders (pairs, spreads), references in another quote currency,
reference indicators inside `htf_filter`, and auto-adding the reference watch at deployment start.

## Runtime parity and observability polish — ✅ Shipped

Paper and live twins exposed four gaps ([ADR 0097](decisions/0097-runtime-parity-and-observability.md)).
Shipped:

- Paper resolves a same-bar exit tie exactly like the backtest: stop, then a touched TP, then the
  signal exit, then the time exit. A bar that trades through the stop no longer exits as a time
  exit at its close. A parity test covers every combination.
- `position_state` (`open_protected` vs `exiting`, plus `flat`, `entering`, `open_unprotected`,
  `open_unverified`) and `exit_in_flight` sit beside the raw `phase` on deployments, positions,
  operator rows and books, and portfolio sleeves. The UI reads "Open · protected (TP/SL resting)"
  instead of `pending_exit`.
- `submit-study --async` waits 30 s for the API's 202 (`--submit-timeout-seconds` overrides it).
- The operator `portfolios` report compares paper/live twins' entry fills: entries rested, filled,
  and expired, fill against the limit, and time to fill.

Ops contract `thytrader-ops-contract-v57`; Alembic stays `0059`.

**Exit gate met:** parity tests prove the backtest and paper take the same exit, on the same bar, at
the same price for every same-bar combination.

**Shipped follow-up:** async study planning runs in the research worker (ADR 0103); synchronous
preflight stays compatible. Portfolio fill comparison UI shipped in ADR 0098, and explicit
paper/live links shipped in ADR 0102 below.


## Library and Portfolio polish — ✅ Shipped

Operator-facing polish after agent research and paper/live twins ran side by side
([ADR 0098](decisions/0098-library-views-book-marks-portfolio-fills.md)). Shipped:

- The Strategies library opens on **Mine**. **Research** (strategies tagged `claude-research` or
  `research-*`) and **All** are one click away, and each viewer's choice is remembered.
  `GET /api/v1/strategies?origin=` and `list-strategies --origin` apply the same filter
  server-side, combined with `tag`.
- An open paper book's `protection_status` is `covered` on every read, matching `position_state`.
  Bounded live reads count a working closing-side order as cover.
- Deployment positions and portfolio sleeve `books[]` carry a last-bar `mark_price` and gross
  `unrealized_pnl` from the decision journal. Bot detail and sleeve rows show state, uPnL, time
  held, and entry / stop / target compactly.
- `GET /api/v1/portfolios/{id}/fill-comparisons` and a **Paper vs live** panel on the Sleeves tab
  compare twins' entry fills (fill rate, bps vs limit, median wait).

Ops contract `thytrader-ops-contract-v58`; Alembic stays `0059`.

**Shipped follow-up:** origin counts on Mine / Research / All reflect all tag-filtered matches
before pagination. Explicit paper/live links shipped in ADR 0102 below.
## Operand-level indicator offsets — ✅ Shipped

[ADR 0099](decisions/0099-operand-level-indicator-offsets.md) adds independent operand offsets
(0–500 completed native-clock bars) across entry, signal-exit, and HTF-filter conditions. Warmup
and dataset coverage include operand lags in research, parameter candidates, paper, and live.
The builder saves these reads; traces and journals expose their exact lagged values. New squeeze
templates share current/prior Bollinger and Keltner definitions and offer their parameter axes,
so optimization keeps prior-bar parameters synchronized. Existing snapshots remain unchanged.

Ops contract `thytrader-ops-contract-v59`; Alembic stays `0059`.

**Exit gate met:** hand-calculated lag/crossover, undefined history, clock alignment, runtime/
research parity, fingerprints, squeeze candidate warmup, and builder save/reload regressions.

## YAML non-secret settings and runtime-reloadable YOLO — ✅ Shipped

Non-secret knobs including YOLO on/off and independent tiers live in `thytrader.yaml`
([ADR 0055](decisions/0055-yaml-settings-runtime-reloadable-yolo.md)). YAML wins leftover env
and applies without restarting API or workers. `THYTRADER_YOLO_TIERS=paper` is a valid leftover
(not JSON). Secrets stay in ignored `.env`. Loopback `/settings` exposes the YOLO toggle, tier
enum, and moved knobs. Live still needs `--i-understand-live`. Playbook never starts live. Extra
exchanges stay out. No ops-contract bump (remain v20 / Alembic `0032` from ADR 0054). Workstation
IA and Coinbase secrets UI ship as ADR 0053 beside this YAML panel.

**Exit gate met:** leftover env `THYTRADER_YOLO_TIERS=paper` boots; YAML `yolo.tiers: paper` after
start allows paper skip-confirm; live skip stays 403 without a `live` tier; Settings page saves
without a process restart.

## Multi-instrument documents and intra-strategy pyramiding — ✅ Shipped

One published document may cover a primary Coinbase USD spot product plus 1–7 extra USD spot
products (at most eight total). The same indicators, entry, sizing, exits, and clocks evaluate
independently on each product. One paper or live start still creates one deployment and one quote
cash book. The worker evaluates covered products in lexicographic `product_id` order on each shared
closed bar; overlay `last_evaluated_bar` is not copied onto the parent until every product finishes
that timestamp ([ADR 0056](decisions/0056-multi-instrument-documents-and-pyramiding.md)).
`portfolio_limits.max_concurrent_positions` 1–8 caps distinct product books, not pyramid lots.
Optional same-side adds require both `entry.pyramiding` (`require_unrealized_profit: true`) and
risk-policy `allow_intra_strategy_pyramiding`. Averaging down is rejected. Paper/live deny
schema-enabled pyramiding without the policy flag (`PYRAMIDING_NOT_ALLOWED`). Backtests follow the
document only. Ops contract is `thytrader-ops-contract-v21` / Alembic `0033`. Extra exchanges stay
out. Deployment HTTP/UI/operator inventory for every product book is [ADR 0060](decisions/0060-multi-book-deployment-api.md)
(`positions`, `instrument_runtimes`, product-tagged orders/fills, `book_totals`; compatibility
`position` stays labeled). No ops-contract bump.

**Exit gate met:** a two-product document locksteps on one bar; an overlay save does not stamp
parent `last_evaluated_bar`; pyramid adds skip `MAX_OPEN_POSITIONS` and fail closed without the
policy flag. HTTP and operator reports name each open book instead of collapsing onto the primary
product.

## Atomic fill ledger — ✅ Shipped

Fill insert and cash/position projection commit together (`economics_applied_at`). Live economics
arrive through venue ingest. Worker lease columns are reserved
([ADR 0057](decisions/0057-atomic-fill-ledger-and-product-isolation.md)). Extra exchanges stay out.

## Protection lifecycle and live capital accounting — ✅ Shipped

Verified attached-child coverage, fenced worker leases, revision-checked writes, stop vs flatten vs
managed shutdown, live capital separate from venue quote, and durable UTC day-open / high-water
baselines ([ADR 0058](decisions/0058-protection-lifecycle-accounting.md)). Default HTTP/CLI stop is
managed shutdown; `--flatten` marketably exits. Live still needs `--i-understand-live`. Ops contract
is `thytrader-ops-contract-v26` / Alembic `0039` with deployment `capital` HTTP fields
([ADR 0065](decisions/0065-deployment-capital-accounting-http.md)), a fourth research engine
([ADR 0066](decisions/0066-research-ops-contract-v4.md)), and explicit breaker latch reset
([ADR 0064](decisions/0064-deployment-http-lifecycle-and-breaker-latch-reset.md); fan-out label
ops v23). Extra exchanges stay out.

**Exit gate met:** missing/canceled children are uncovered; protection runs when entries are paused;
STOPPED residual occupancy remains in account risk; venue quote does not overwrite ledger cash.

## Destination capabilities (accepted; not current Builder order)

These are product destination, not the next Thy Builder slice. Do not implement them by silently
widening schema, clocks, or live safety. Each needs its own ADR and tests when sequenced.

| Capability | Shipped today | Destination |
|---|---|---|
| Exchange | Coinbase Advanced Trade spot | Same, until trustworthy; **other exchanges later** |
| Portfolio | Balances, valuation history, fees, plus Phase 10 registry (slots, allowlist, paper book, allocations); on-demand entries use the same registry; daily-loss / drawdown breakers, order-rate limits, and reference-price collars ([ADR 0050](decisions/0050-daily-loss-drawdown-rate-collars.md)) | Max order qty/notional beyond exposure fractions; consecutive-error breaker; kill-switch vs trapped-position behavior |
| On-demand trades with SL/TP | Yes, long or short via intent + risk; live attaches entry brackets when trailing is off ([ADR 0039](decisions/0039-on-demand-discretionary-trades.md), [ADR 0045](decisions/0045-spot-shorting-and-attached-entry-brackets.md)). Published-strategy same-side adds are [ADR 0056](decisions/0056-multi-instrument-documents-and-pyramiding.md) | On-demand scale-in remains out |
| Dataset TFs | 1m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 1d complete-only; ranged newest-first ingest (350-bar pages, fair per-cycle budgets, 429 backoff) with research lookbacks from 90 days (1m) to 10 years (2h-1d), watched-only ingest, and sub-second catalog listings; ops contract v45 / Alembic 0052 ([ADR 0085](decisions/0085-fast-research-ingest.md)); thin markets keep confirmed no-trade bars as flat bars, with listing-only floors ([ADR 0095](decisions/0095-sparse-markets-no-trade-bars-listing-floors.md)) | Same Coinbase-listed intervals |
| Strategy / paper / live clocks | All ingested venue TFs ([ADR 0040](decisions/0040-venue-strategy-paper-live-htf-clocks.md)) | Same clocks as ingested venue TFs; extra listed granularities still need their own ADR |
| Indicators | 53-kind fail-closed catalog through [ADR 0086](decisions/0086-indicator-catalog-expansion-and-offset.md) (trend, momentum, volatility, volume, statistical, and price kinds with series ids; per-declaration and per-operand `offset` bar lag ([ADR 0099](decisions/0099-operand-level-indicator-offsets.md))); optional per-indicator TFs ([ADR 0042](decisions/0042-per-indicator-timeframes.md)) | Further bounded kinds without TA passthrough |
| Research | Single-instrument backtests; HTF filter in research, paper, and live ([ADR 0025](decisions/0025-multi-timeframe-htf-filter.md), [ADR 0041](decisions/0041-paper-live-htf-filter-evaluation.md)); Phase 11 OOS / walk-forward / cross-market studies; parameter sweeps, WFO, and stitched OOS equity ([ADR 0044](decisions/0044-parameter-sweeps-wfo-stitched-equity.md)); richer sweep axes and persisted study catalog ([ADR 0052](decisions/0052-richer-sweep-axes-study-catalog.md)); auto-bound catalog datasets, omitted study bounds, cross-market variants from one strategy, and 64-candidate async sweeps ([ADR 0089](decisions/0089-agent-research-ergonomics.md)) | Further composed research remaining destination |
| Deploy | Concurrent paper/live under the shared registry (Phase 10); paper deploy sets documented maker/taker assumptions ([ADR 0048](decisions/0048-paper-deploy-fee-fields.md)); one document may cover multiple Coinbase USD spot products with optional intra-strategy pyramiding ([ADR 0056](decisions/0056-multi-instrument-documents-and-pyramiding.md)) | Extra exchanges stay out |
| Automation after deploy | Execution worker on closed bars | Same; no babysitting required |
| Agent E2E | Six lane-separated skills plus playbook; YOLO `live` may skip `--confirm` on live start/pause/resume/stop ([ADR 0043](decisions/0043-yolo-live-skip-confirm.md)); YAML YOLO applies without restart ([ADR 0055](decisions/0055-yaml-settings-runtime-reloadable-yolo.md)); `--i-understand-live` remains | Primary surface complete for research, build, deploy, monitor, journal, notify (Phases 12–14, ADR 0030 / 0037 / 0043 / 0055). In-app operator chat is a separate destination row |
| Trade-reason journals | Per-intent `thytrader-trade-reason-v1` with strategy snapshot identity, closed-bar signal, risk verdict, notes, and ledger facts on read ([ADR 0054](decisions/0054-trade-reason-journals.md)). Same payload for UI and operator reports | Richer review layout stays with workstation IA. Extra exchanges stay waiting |
| Decision timeline | Per-bar `thytrader-bar-decision-v1` journal for every paper/live strategy bot: outcome, reason, rule values versus thresholds, risk verdict, linked orders, position; deployment and strategy HTTP pages, operator `decisions`, `thytrader-runtime decisions`, bot detail and Why timelines; ops contract v47 / Alembic 0053 ([ADR 0087](decisions/0087-per-bar-decision-timeline.md)) | Backtest per-bar explanations and alerts on outcomes are not built |
| Experiential trainer | V1 fail-closed integer ranker over attributed local journals ([ADR 0049](decisions/0049-experiential-train-v1.md)); advisory research input only | Richer learners. Not a live brain |
| In-app operator chat | Loopback `/chat` and `/api/v1/operator-chat`; user-pasted LLM key in the API process; closed catalog of gated skill-lane HTTP tools ([ADR 0051](decisions/0051-in-app-operator-chat.md)). Coinbase keys stay off this surface | Not a substitute for `ops/` skills. Extra exchanges stay waiting |
| Workstation IA | Four-destination rail (Home, Strategies, Portfolio, Trade) plus a System group (Settings, Audit log, Journal, Memory & why-trade), ⌘K command palette, Agent side panel hosting operator chat on every page (`/chat` kept as full page), and light/dark design tokens ([ADR 0079](decisions/0079-four-destination-shell-agent-panel-palette-tokens.md), superseding [ADR 0053](decisions/0053-workstation-ia-write-only-coinbase-credentials.md) in part). Each strategy has one workspace (Build · Test · Run · Why at `/strategies/{id}`, `/test`, `/run`, `/why`) with a library evidence pipeline, and a live preflight from existing endpoints ([ADR 0080](decisions/0080-per-strategy-workspace-build-test-run-why.md)); `/research`, `/deploy`, `/backtests` links redirect or show a chooser. Portfolio groups and filters bots with truthful per-mode capital totals, bot detail and Trade are recomposed, and a route-declared amber live strip and frame mark every live context ([ADR 0081](decisions/0081-live-chrome-portfolio-bot-detail-trade.md)). Home shows independently loading KPI tiles (portfolio value with 24h change, available quote and live-bot reservations, live exposure and protection, bot counts), a 1D/1W/1M/3M chart, Needs attention aggregated from bots, credentials and risk policy, watched datasets, and research jobs, Your bots, compact Holdings, the fee tier, and a Data health disclosure, all from existing endpoints ([ADR 0084](decisions/0084-home-kpis-needs-attention-data-health.md)) | Portfolio foundation shipped ([ADR 0088](decisions/0088-portfolio-model-and-portfolio-backtest.md)): portfolios with sleeves, cash reserve, shared limits, manager settings, an append-only journal, and portfolio backtests on Portfolio and `thytrader-portfolio`. Portfolio deployment shipped ([ADR 0091](decisions/0091-portfolio-deployment-limits-and-manager-proposals.md)): one bot per sleeve, portfolio caps and latched breakers in the risk gate, manager proposals with Approve / Decline / Ask why, and the manager briefing; an in-app manager agent, cross-sleeve netting, and simulating portfolio caps in backtests remain. Keep those surfaces uncluttered. Do not weaken safety copy or confirmation. YAML/YOLO stays the ADR 0055 panel beside Coinbase credentials |
| Strategy model | One mutable strategy per id with revision-guarded saves (invalid work in progress allowed), automatic content-addressed snapshots at backtest/study/deploy start, `strategy_id` foreign keys on every run and bot, Current rules / Earlier edit with a What-changed diff, guided Update bot, hard delete and bulk delete with a running/paused-bot block and kept live history; ops contract v41 / Alembic 0048 ([ADR 0082](decisions/0082-strategy-root-mutable-strategies-auto-snapshots.md)) | Backtest simulation is the next row ([ADR 0083](decisions/0083-unified-backtest-model.md)) |
| Backtest model | One unversioned model, `engine: "thytrader-backtest"`, replaced every earlier engine: resting maker-limit entries, taker stops/time exits/end-of-window liquidation with fixed slippage, optional disclosed `spread_bps` stress, and `validity_limits` on every summary. No engine selector; `GET /api/v1/research/backtest-model` / `thytrader-research backtest-model` describe it. Ops contract v42 / Alembic 0049 deletes research runs, results, jobs, and studies written by the retired engines ([ADR 0083](decisions/0083-unified-backtest-model.md)) | Latency, rejection, partial-fill, and queue-position models remain deferred |
| Coinbase secrets UI | Loopback Settings form beside YAML/YOLO plus `thytrader-runtime` show/set/clear ([ADR 0053](decisions/0053-workstation-ia-write-only-coinbase-credentials.md)). Keys stay server-side. GET never echoes them. YOLO never covers set/clear. Extra exchanges out | Same write-only contract. LLM keys stay with operator chat ([ADR 0051](decisions/0051-in-app-operator-chat.md)). Non-secret YAML/YOLO stays ADR 0055 |
| Mermaid schemas and contracts | Contributor [contract diagrams](architecture/contracts/README.md) for strategy, research-run spec, backtest result, ops contract, order-intent → risk → broker, and other durable payloads | Keep diagrams in sync when those contracts change. Link from contributor docs. Do not dump on the landing README |

## Phase 0: Repository foundation — ✅ Complete

- Architecture and product documentation.
- Repository-specific `AGENTS.md`.
- GitNexus semantic and PDG indexing workflow.
- Python package layout and FastAPI application skeleton.
- SvelteKit/Svelte 5 strict-TypeScript application.
- Formatting, linting, type checking, tests, and CI.
- Docker Compose clone-and-run stack for PostgreSQL, migration, API, worker, and web.
- PostgreSQL migrations and configuration validation.
- `.env.example` with placeholders only.
- Structured logging and secret redaction.
- Bootstrap regression coverage for deterministic image build, healthy database, one-shot migration,
  and health-gated API/worker/web startup ordering.
- Shared immutable market-data volume: the worker publishes datasets and the API consumes the same
  artifacts through a read-only mount.

**Exit gate met:** from clean project-owned Docker state, the documented bootstrap built every image,
started healthy PostgreSQL, applied migrations `0001` through `0009` (including durable strategy
draft/publication lifecycle storage), and returned only after the API,
portfolio worker, market-data worker, and web UI were healthy. Runtime proof covered live read-only
Coinbase portfolio access, worker publication and API discovery of a verified dataset, deterministic
browser-facing strategy publication and idempotent backtest retrieval, in-place service restart, and a
full `docker compose down` plus bootstrap restart with PostgreSQL and Parquet identities retained. The
shutdown released every project port and left no running project container.

## Phase 1: Read-only Coinbase and portfolio visibility — ✅ Complete

### Completed

- Provider-neutral exchange account contracts (`exchanges/protocols.py`, `exchanges/models.py`).
- Coinbase Advanced Trade adapter using the official SDK (`exchanges/coinbase.py`).
- Coinbase authentication and credential configuration with `SecretStr`-backed values.
- Permission display without rejecting keys that have additional permissions.
- Account balances, portfolio valuation (Decimal-precise), and demo fallback.
- Scheduled snapshot worker: startup observation, configurable interval, demo skip, error retry.
- Persisted portfolio valuation history with append-only snapshots.
- Read-only history chart with range filtering (24H/7D/30D/All), gain/loss, gap handling, and freshness. Portfolio history and backtest equity use TradingView Lightweight Charts; missed snapshot duration stays visible and is not interpolated.
- Dashboard with connection status, staleness indicators, and redacted diagnostics.
- API `/health/ready` and `/health/live` endpoints.
- Secret redaction in logs, test fixtures, and API responses.
- Structured append-only operational audit trail (`persistence/audit_events.py`, migration `0010_audit_events.py`, `persistence/postgres_audit_events.py`, `/api/v1/audit-events`, and `/audit` UI route).
- Coinbase fee-tier visibility and 30-day volume display (`exchanges/fees.py`, `/api/v1/fees`, and dashboard fee panel).
- Market-data freshness contract (<2h05m threshold) and dedicated dashboard badge (`market_data/freshness.py`, `/api/v1/market-data/freshness`).
- Coinbase Advanced Trade public WebSocket market ticker streaming lifecycle with heartbeat-timeout supervision and reconnect backoff (`exchanges/ws/`).

**Exit gate met:** a user can connect an operator-selected key and observe an accurate, reconcilable
portfolio — including live market data, fees, and audit trail — without enabling order submission.

## Phase 2: Historical data and strategy definitions — ✅ Shipped (narrow V1)

Phase 2 is the shipped market-data and strategy foundation used by research, paper, and live. It is
not an open near-term sequence. Remaining destination (multi-instrument documents, a wider
indicator catalog) lives in
[Destination capabilities](#destination-capabilities-accepted-not-current-builder-order).

### Phase 2A: Market-data pipeline

#### Completed range-ingestion increment

- Read-only USD spot-product catalog plus selectable 1h data-source diagnostics.
- Coinbase product-constraint and bounded recent-candle adapter through the official SDK.
- Closed-candle validation, gap/missing-interval detection, and freshness facts.
- Bounded 1h historical range ingestion with non-overlapping Coinbase pagination (350 candles/page,
  2,160 candle / 90-day maximum).
- Seven-day range-completeness report endpoint with expected vs received counts and binary coverage.
- Immutable date-partitioned Parquet writer with JSON manifests, completeness facts, and SHA-256
  content fingerprints, wired only to the dedicated scheduled ingestion worker.
- Deterministic demo diagnostics plus a visible dashboard connection/integrity panel.
- Separately supervised market-data worker with its own readiness, restart boundary, persistent Parquet
  volume, complete-only publication, automatic retry, and durable PostgreSQL success/failure state.
- Read-only API and dashboard coverage/freshness/fingerprint/failure diagnostics.
- Continuous cumulative 1h maintenance: durable planning from last verified coverage, one-candle
  overlap, deterministic merge, no-op current cycles, immutable revisions, and fingerprint lookup.
- Capped exponential retry scheduling with jitter and prior-revision preservation across failures.
- Versioned operator diagnostics (`thytrader-operator`, `GET /api/v1/operator/*`) for freshness and gaps.

This proves the read-only provider, validation, continuous 1h maintenance, and fingerprint-addressed
dataset paths. It is deliberately **not** a price chart, market signal, or backtest engine. See the
[market-data pipeline](architecture/market-data.md) for its explicit contract and limits.

#### Remaining

None for currently listed Coinbase granularities. 5m live on the same published clock shipped in
**Phase 13**. Venue datasets and strategy/paper/live clocks now cover `1m`–`1d` (Phase 7, ADR 0038,
ADR 0040). Watchlist ingest covers multiple products. Future Coinbase-listed granularities still
need their own complete-only ADR.

**1h exit gate met:** validated, gap-checked historical candles are queryable by immutable dataset
fingerprints that future backtests can reference for reproducibility. Multi-timeframe and
multi-product watchlist expansion for currently listed Coinbase TFs is shipped.

The gate has live PostgreSQL evidence: the full migration chain runs on PostgreSQL 18, two
independent database engines prove stale retry-generation claims are rejected atomically, and the
installed worker plus deterministic acceptance drill cover initial publication, no-op and
incremental boundaries, corrupt-manifest reconciliation, provider failure, restart backoff,
readiness, and graceful shutdown.

### Phase 2B: Canonical strategy schema — ✅ Narrow V1 shipped

- ✅ Backend-validated immutable publication for the conservative reference profile (see
  [canonical strategy schema](architecture/canonical-strategy-schema.md)).
- ✅ Canonical SHA-256 strategy fingerprints and verified immutable-dataset bindings.
- ✅ Bounded indicator registry: EMA, SMA, RSI, ATR, volume SMA, highest, lowest, stdev, ROC, Williams %R, CCI, WMA, momentum, MFI, MACD, Bollinger, identity OHLCV, and constant.
- ✅ Typed comparisons and bounded recursive AND/OR/NOT condition groups.
- ✅ Conservative reference EMA trend profile with durable browser draft recovery, typed authoring API,
  immutable publication, verified-dataset backtest workflow, and bounded human-readable summary.
- ✅ Mutable PostgreSQL drafts retain the server-owned identity and validated definition across browser
  reloads with optimistic revision checks that reject stale saves. Publication saves, publishes, and
  consumes the matching draft atomically; separately stored append-only archive markers hide published
  versions from active selection without altering their canonical bytes or fingerprints.
- ✅ Editing a published version into an explicit next version (`POST /api/v1/strategies/{id}/revise`)
  and a 500-character user-authored description field.

**Exit gate:** the same immutable strategy version can be validated and associated with a
reproducible dataset snapshot.

> Superseded in part by [ADR 0082](decisions/0082-strategy-root-mutable-strategies-auto-snapshots.md):
> drafts, publication, revise, archive markers, and version numbers were replaced by one mutable
> strategy with automatic snapshots. The bullets above record what shipped at the time.

**Declarative publication exit gate met:** the implemented indicator and recursive-condition
language can be validated, published, verified by fingerprint, and durably associated only with a
verified dataset fingerprint. Unsupported sizing/stop variants and a visual node canvas stay later
(not a Phase 2B leftover list). Multi-instrument documents remain destination.

## Phase 3: Backtesting

- ✅ Strict canonical immutable research-run specifications bind exact published strategy and verified
  dataset fingerprints to half-open evaluation/warmup ranges, exact capital/fee/slippage assumptions,
  completed-close signal timing, an explicit seed, and an engine identity (today the single
  `thytrader-backtest`).
- ✅ Append-only PostgreSQL publication requires the existing exact strategy/dataset binding and
  reverifies canonical bytes, denormalized row identity, immutable artifacts, coverage, and the final
  fill-lookahead candle on every load.
- ✅ Versioned deterministic indicator and entry-condition evaluation for published runs, with strict no-lookahead candle selection, canonical
  fingerprinted traces, and a read-only CLI. Traces are ephemeral and are not backtest results.
- ✅ First event-driven long or short single-position bar simulation with private Decimal64
  arithmetic, strict no-lookahead signal boundaries, next-open marketable fills, ATR sizing, adverse
  fixed slippage, taker fees, initial-stop/take-profit/time-exit state, conservative same-bar stop-first
  ordering, canonical trade/equity/drawdown/metrics output, append-only PostgreSQL results, and a
  read-only simulation CLI. Superseded by the unified model below.
- ✅ Browser/API research flow creates a durable conservative strategy draft, validates/publishes immutable
  strategy evidence, explicitly selects an exact version, reverified compatible 1h dataset, period,
  capital, fees, slippage, engine, and spread stress, submits/reuses deterministic backtests, loads
  all results per exact strategy version, compares the newest result across versions, and opens the
  immutable result detail. It cannot mutate results or grant trading authority.
- ✅ Later engines added a disclosed constant spread stress and maker-limit bar fills aligned with
  paper and live, running in parallel with the first simulator.
- ✅ One unified backtest model, `engine: "thytrader-backtest"`
  ([ADR 0083](decisions/0083-unified-backtest-model.md)), replaced every earlier engine: resting
  post-only entries at the signal close filled on trade-through (maker fee, no slippage), stop-first on
  the fill candle, resting take-profit before stop on later candles, taker stops/time exits with fixed
  slippage, liquidation at the open of the `evaluation.ends_at` candle, optional `costs.spread_bps`
  stress (not observed book data), and `validity_limits` on every summary. There is no engine
  selector. Latency, rejection, partial-fill, and queue-position models remain deferred. See
  [backtest simulation](architecture/backtest-simulation.md).
- ✅ Phase 10 risk-policy registry and concurrent single-instrument paper/live (ADR 0033). Daily-loss / drawdown, order-rate limits, and collars are ADR 0050. Multi-instrument documents and intra-strategy pyramiding are ADR 0056.
- ✅ ATR-multiple trailing-stop state machine in backtest, paper, and live (Phase 13 / ADR 0036).
- ✅ Phase 11 OOS holdout, walk-forward validation, and cross-market studies (ADR 0035). Parameter sweeps, WFO, and stitched OOS equity shipped as ADR 0044. Richer sweep axes and persisted study catalog shipped as ADR 0052.
- ✅ Deterministic versioned `thytrader-buy-and-hold-v1` benchmark comparison derived from the reverified result, source run, and immutable dataset. It uses the backtest model's taker semantics for both legs (taker fee, fixed slippage, half the stressed spread), marks at the stressed bid, carries `engine`, reports return/drawdown/cost evidence, and is exposed as a separate read-only API/dashboard comparison. See [derived buy-and-hold benchmark](decisions/0011-derived-buy-and-hold-benchmark.md).

### Next delivery increment

The browser/API research loop is implemented: it saves one mutable strategy in place (invalid work in
progress allowed), presents a bounded semantic summary plus a **How backtests simulate** disclosure, launches
a backtest or a composed OOS/walk-forward/sweep/WFO study from the current definition (each
start snapshots it), and marks every result Current rules or Earlier edit
([ADR 0082](decisions/0082-strategy-root-mutable-strategies-auto-snapshots.md)).

Paper and live share one execution worker. Maker entries are implemented (`limit_limit_gtc` +
`post_only`); stops and time-exits are marketable sells. Live orders use Advanced Trade REST v3 JSON
with `RESTClient` only as signed HTTP. Phase 13 extras are shipped. Remaining deferred: operator
runbook drills; destination remainders in the table above.

**Exit gate met for the research slice:** reference-strategy results are deterministic, disclose
assumptions, resist lookahead, and pass adversarial fill/risk tests.

## Phase 4: Paper execution — ✅ Complete (narrow 1h|5m maker loop)

- Persistent simulated broker using normalized 1h or 5m candle-close events (live 5m arrived in Phase 13).
- Same snapshotted strategy semantics used by backtests/live trading.
- Continuous `thytrader-execution-worker` supervision.
- Deploy tab with pause/resume/stop, position, orders, fills, and reject reasons.

**Exit gate met:** a user can deploy one published strategy to paper mode; the worker evaluates each
closed 1h or 5m candle once, records intents/fills/position, and obeys pause and stop.

## Phase 5: Live maker execution — ✅ Narrow path complete; extras deferred

- Deploying `live` is the arming action; credentials required.
- Idempotent maker entries and ordinary take-profit orders via REST v3 JSON.
- Paginated fills as the fill ledger; GET-order after submit. List Fills continues on the
  documented `cursor` (not `has_next`), converts `size_in_quote`, and quarantines incomplete
  rows ([ADR 0059](decisions/0059-coinbase-list-fills-cursor-pagination.md)).
- Marketable stop and time-exit sells.
- Phase 13 later shipped native OCO brackets, user-order WebSockets, and ATR trailing.
  Remaining deferred: operator runbook drills.

## Phase 6: Operator and agent integration — ✅ Complete

Agent integration begins earlier as each supported interface becomes available; this phase completes
the full operational surface. It must not wait for live trading, and it must not give an agent trading
authority merely because it can inspect a system.

- ✅ Stable versioned read-only diagnostics API and CLI, starting with health, configuration validity,
  market-data quality, published-strategy state, and backtest evidence.
- ✅ Redacted health/configuration/data-quality/performance reports.
- ✅ In-repo ThyTrader operator skill.
- ✅ Machine-readable schemas and compatibility checks (`schema-check` plus committed JSON Schema).
- ✅ Explicitly separated, confirmation-gated research mutation tools for strategy authoring (drafts and
  immutable publication at the time; one mutable strategy with automatic snapshots since ADR 0082)
  and backtest submission. These are distinct from paper/live authority.
- ✅ HTTP-first operator and research CLIs (loopback API by default; `--local` is explicit).
- ✅ Read-only runtime watch (`thytrader-operator runtime`).
- ✅ Separate confirmation-gated `thytrader-runtime` skill/CLI for paper/live start, pause, resume, and
  stop. Live start requires `--confirm` and `--i-understand-live`.

**Exit gate:** an external agent can diagnose a running instance and, with `--confirm`, mutate research
artifacts using supported HTTP interfaces without database access, secret exposure, or implicit
trading authority. Paper/live control is a third confirmation-gated surface, not part of operator or
research skills.

Further agent E2E orchestration and YOLO opt-in are **Phase 12** and are now shipped (playbook over
existing CLIs; YOLO default off; live `--confirm` skip is [ADR 0043](decisions/0043-yolo-live-skip-confirm.md)).
They were not part of the Phase 6 exit gate. See
[Phase 12](#phase-12-agent-orchestration--yolo-opt-in--shipped).

## Atomic portfolio creation with sleeves — ✅ Shipped

[ADR 0101](decisions/0101-atomic-portfolio-creation-with-sleeves.md) closes ADR 0094's deferred
`create --file` with sleeves: the existing portfolio-create API and confirmed CLI accept up to
32 distinct initial sleeves. Shared batch validation, sorted strategy locks, and one transaction
persist the portfolio, sleeves, and journal at revision 1 or nothing. Empty creation remains
supported. Creation saves definitions only; deploying and arming remain separate runtime actions.
Ops contract `thytrader-ops-contract-v61` advertises `create_with_sleeves`; Alembic stays `0059`.

## Open-book PnL after paid entry fees — ✅ Shipped

[ADR 0100](decisions/0100-fee-adjusted-open-book-pnl.md) adds nullable `entry_fees` and
`unrealized_pnl_net` to marked deployment positions (summary and full) and portfolio sleeve books.
The shared UI prefers net, labels a gross fallback, and states that future exit fees are excluded.
Applied local fills establish paid fees; partial exits retain only the surviving share and adds
accumulate fees. Missing/mismatched evidence or more than 1000 current-window fills stays unknown.
Existing cash/equity/risk accounting is unchanged. Ops contract `thytrader-ops-contract-v60`
advertises `fee_adjusted_book_pnl`; Alembic remains `0059`. Exit-fee estimates remain deferred.


## Explicit paper/live twins — ✅ Shipped

[ADR 0102](decisions/0102-explicit-paper-live-twin-links.md) replaces newest-by-fingerprint
pairing with durable operator-selected one-to-one links. Bot detail and confirmed runtime CLI /
HTTP controls link and unlink comparable strategy bots without lifecycle or order authority.
Comparisons use saved pairs only, including several pairs on one snapshot; worker saves and
restarts preserve the relationship. Existing bots remain unlinked until selected.
Ops contract `thytrader-ops-contract-v62` advertises `explicit_deployment_twins`; Alembic `0060`.
Study planning in the worker, quiet-bar policy, and library origin counts remain separate work.

## October handoff completion — ✅ Shipped

[ADR 0103](decisions/0103-worker-planned-async-studies.md) moves async study planning into the leased
research worker. [ADR 0104](decisions/0104-bounded-newest-candle-wait.md) adds a bounded decision-candle
publication wait with entries blocked and maintenance continuing. Mine / Research / All show
server-derived tag-filtered counts before pagination. Ops contract v63, Alembic remains 0060.

[ADR 0105](decisions/0105-rule-equivalent-clone-twins.md) completes existing twin linking for
separately authored clones: server-verified exact pinned trading rules, independent snapshot
identities in each comparison side, and unchanged comparison-only authority.

## Portfolio accounting corrections — ✅ Shipped

The manager briefing now supplies the same journal-based marks and paid-entry-fee PnL as
portfolio deployment reads, including when recent decisions are omitted. Working entry exposure
and capital reservations exclude verified exit intents, including paper take-profit limits;
partial entry remainders and orders missing intent evidence remain counted. These fixes restore
the existing ADRs 0058, 0091, 0098, and 0100 contracts without changing schemas, migrations,
confirmation gates, or configured limits.
