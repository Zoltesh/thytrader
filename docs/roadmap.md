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
3. **Permit bounded research automation:** ✅ confirmation-gated `thytrader-research` CLI and skill (drafts, publish, backtests, and composed studies).
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
   gap inspection covers the full watch window, and every HTTP agent CLI fails closed on an unequal
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
`timeframe` remains the `1h`|`5m` decision clock in this slice. Research engines V1/V2/V3 evaluate last-completed
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
   warmup → undefined/null (not 0), tri-state conditions. Research V1/V2/V3, paper, and live share
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
honestly in the operator catalog and engine-support matrix.

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

Walk-forward / out-of-sample / cross-market studies compose existing bar-backtest V1/V2/V3 engines
([ADR 0035](decisions/0035-phase-11-research-rigor.md)). `ema-trend` remains the default draft
template; `rsi-mean-reversion`, `macd-trend`, and `bollinger-mean-reversion` are additional starting
drafts. The engine-support matrix has V1, V2, and V3 columns. Walk-forward is **validation**, not
parameter optimization. Child backtests remain the append-only evidence; there is no Alembic
revision. Cross-market still requires one published single-instrument strategy per product.

**Exit gate met:** agents can `plan-study` / `submit-study --confirm` for OOS holdout, rolling or
anchored walk-forward, and 2–8 product cross-market studies; the UI can launch OOS and walk-forward
on a published fingerprint; templates are selectable; the matrix names V3 honestly.

Parameter sweeps, walk-forward optimization, and stitched OOS equity shipped later as
[ADR 0044](decisions/0044-parameter-sweeps-wfo-stitched-equity.md). Paper/live HTF is
[ADR 0041](decisions/0041-paper-live-htf-filter-evaluation.md).

## Parameter sweeps / WFO / stitched OOS equity — ✅ Shipped

Research composition can `plan-study` / `submit-study --confirm` for `parameter_sweep` and
`walk_forward_optimization` without inventing grid math or looking ahead from OOS into selection
([ADR 0044](decisions/0044-parameter-sweeps-wfo-stitched-equity.md)). Child evidence stays V1/V2/V3.
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
`long`). Geometry: longs `stop < entry < take_profit`; shorts invert. Paper and backtest V1/V2/V3
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
V1/V2/V3 golden fingerprints unchanged.

## Wider fail-closed indicator catalog — ✅ Shipped

Stochastic `%K`/`%D`, Wilder ADX / `+DI` / `-DI`, configurable rolling OHLCV inputs, and sample
stdev join the fail-closed registry ([ADR 0047](decisions/0047-wider-fail-closed-indicator-catalog.md)).
Same complete-only candles, last-completed per-indicator clocks, and published strategy semantics in
backtest, paper, and live. No TA-library passthrough. No interpolated candles. Population `stdev`
and Bollinger bands are unchanged.

**Exit gate met:** kinds named in the ADR, implemented in the registry and evaluator, referenced from
conditions, and listed in operator `indicators` plus the engine-support matrix.

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
interpolated. Output is advisory research input. `create-draft --experiential-model-id` is HTTP-only
and merges that advisory into JSON. YOLO never covers `train`. Journal schema and review UI stay
with the sibling why-trade slice; this trainer consumes `JournalEntry` as stored. Extra exchanges
stay out. Ops contract is `thytrader-ops-contract-v17` / Alembic `0029`.

**Exit gate met:** `uv run thytrader-memory train --origin agent --confirm` writes a fingerprintable
model; `list-models` / `show-model` read it; research can attach the advisory without changing
strategy semantics or placing orders.

## Destination capabilities (accepted; not current Builder order)

These are product destination, not the next Thy Builder slice. Do not implement them by silently
widening schema, clocks, or live safety. Each needs its own ADR and tests when sequenced.

| Capability | Shipped today | Destination |
|---|---|---|
| Exchange | Coinbase Advanced Trade spot | Same, until trustworthy; **other exchanges later** |
| Portfolio | Balances, valuation history, fees, plus Phase 10 registry (slots, allowlist, paper book, allocations); on-demand entries use the same registry | Daily-loss / drawdown breakers, order-rate limits, reference-price collars |
| On-demand trades with SL/TP | Yes, long or short via intent + risk; live attaches entry brackets when trailing is off ([ADR 0039](decisions/0039-on-demand-discretionary-trades.md), [ADR 0045](decisions/0045-spot-shorting-and-attached-entry-brackets.md)) | Intra-strategy pyramiding |
| Dataset TFs | 1m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 1d complete-only | Same Coinbase-listed intervals |
| Strategy / paper / live clocks | All ingested venue TFs ([ADR 0040](decisions/0040-venue-strategy-paper-live-htf-clocks.md)) | Same clocks as ingested venue TFs; extra listed granularities still need their own ADR |
| Indicators | Fail-closed catalog through [ADR 0047](decisions/0047-wider-fail-closed-indicator-catalog.md) (`stochastic`/`adx` series ids, configurable rolling inputs, `stdev_sample`); optional per-indicator TFs ([ADR 0042](decisions/0042-per-indicator-timeframes.md)) | Further bounded kinds without TA passthrough |
| Research | Single-instrument backtests; HTF filter in research, paper, and live ([ADR 0025](decisions/0025-multi-timeframe-htf-filter.md), [ADR 0041](decisions/0041-paper-live-htf-filter-evaluation.md)); Phase 11 OOS / walk-forward / cross-market studies; parameter sweeps, WFO, and stitched OOS equity ([ADR 0044](decisions/0044-parameter-sweeps-wfo-stitched-equity.md)) | Richer sweep axes, persisted study rows if operators need a catalog |
| Deploy | Concurrent single-instrument paper/live under the shared registry (Phase 10); paper deploy sets documented maker/taker assumptions ([ADR 0048](decisions/0048-paper-deploy-fee-fields.md)) | Multi-instrument strategy documents and intra-strategy pyramiding remain destination |
| Automation after deploy | Execution worker on closed bars | Same; no babysitting required |
| Agent E2E | Six lane-separated skills plus playbook; YOLO `live` may skip `--confirm` on live start/pause/resume/stop ([ADR 0043](decisions/0043-yolo-live-skip-confirm.md)); `--i-understand-live` remains | Primary surface complete for research, build, deploy, monitor, journal, notify (Phases 12–14, ADR 0030 / 0037 / 0043). In-app operator chat is a separate destination row |
| Trade-reason journals | Phase 14 origin-attributed hooks (journals, sentiment/pattern, monitor, notify). No per-trade “why” record | A human or agent can open a trade and see **why it was made** — signal, published strategy version, risk decision, discretionary note, fill/reconcile facts. Durable, attributed, redacted. Same record for UI and operator reports. No interpolated candles |
| Experiential trainer | V1 fail-closed integer ranker over attributed local journals ([ADR 0049](decisions/0049-experiential-train-v1.md)); advisory research input only | Richer learners. Not a live brain |
| In-app operator chat | Lane-separated skills plus playbook; no in-app LLM chat | Loopback chat; the user supplies **their** LLM API key. The chat is an **operator** using gated skills (same lanes as `ops/`). Confirmation-gated; live still `--i-understand-live` (or the HTTP equivalent). LLM keys stay server-side; Coinbase keys never go to the browser. Not a substitute for skills; it uses them |
| Workstation IA | Capable SvelteKit workstation; strategy / backtest / research / paper-live share crowded library and deploy surfaces | Strategy create, backtest, research, and paper/live deploy are first-class, visible, uncluttered professional surfaces. Use screen real estate. Functionality is obvious without hunting. Modernize; do not weaken safety copy or confirmation |
| Coinbase secrets UI | Server-side `.env` / Compose secrets; UI never receives keys; `.env.example` is names/placeholders only | Loopback form to **set / rotate / clear** Coinbase Advanced Trade API credentials. Keys stay server-side. The UI never echoes them, never logs them, never puts them in browser payloads. View + Trade is enough; extra permissions are reported, not treated as consent |
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

**Declarative publication exit gate met:** the implemented indicator and recursive-condition
language can be validated, published, verified by fingerprint, and durably associated only with a
verified dataset fingerprint. Unsupported sizing/stop variants and a visual node canvas stay later
(not a Phase 2B leftover list). Multi-instrument documents remain destination.

## Phase 3: Backtesting

- ✅ Strict canonical immutable research-run specifications bind exact published strategy and verified
  dataset fingerprints to half-open evaluation/warmup ranges, exact capital/fee/slippage assumptions,
  completed-close/next-open timing, an explicit seed, and the implemented request-contract version.
- ✅ Append-only PostgreSQL publication requires the existing exact strategy/dataset binding and
  reverifies canonical bytes, denormalized row identity, immutable artifacts, coverage, and final
  next-open fill data on every load.
- ✅ Versioned deterministic indicator and entry-condition evaluation for executable
  `thytrader-bar-signal-v1` publications, with strict no-lookahead candle selection, canonical
  fingerprinted traces, and a read-only CLI. Traces are ephemeral and are not backtest results.
- ✅ `thytrader-bar-backtest-v1` event-driven long or short single-position simulation with private Decimal64
  arithmetic, strict no-lookahead signal boundaries, next-open marketable fills, ATR sizing, adverse
  fixed slippage, taker fees, initial-stop/take-profit/time-exit state, conservative same-bar stop-first
  ordering, forced final next-open liquidation, canonical trade/equity/drawdown/metrics output, append-only
  PostgreSQL results, and a read-only simulation CLI. See [backtest simulation](architecture/backtest-simulation.md).
- ✅ Browser/API research flow creates a durable conservative strategy draft, validates/publishes immutable
  strategy evidence, explicitly selects an exact version, reverified compatible 1h dataset, period,
  capital, fees, slippage, V1/V2 engine, and V2 spread, submits/reuses deterministic backtests, loads
  all results per exact strategy version, compares the newest result across versions, and opens the
  immutable result detail. It cannot mutate results or grant trading authority.
- ✅ `thytrader-bar-backtest-v2` uses the same deterministic single-position event ordering with a canonical,
  disclosed constant-basis-point spread stress model: ask-side entries, bid-side exits and triggers,
  bid-close equity marking, executable-entry sizing, immutable fill-level evidence, and zero-spread
  economic regression to V1. V1 result bytes remain loadable/reverifiable unchanged; V2 is not
  observed order-book data or a live-fill prediction.
- ✅ `thytrader-bar-backtest-v3` maker-limit bar fills. Bar-level latency, rejection, and
  partial-fill models beyond that V3 contract, and full order-book queue simulation, remain deferred.
- ✅ Phase 10 risk-policy registry and concurrent single-instrument paper/live (ADR 0033). Intra-strategy pyramiding, multi-instrument strategy documents, and destination circuit breakers remain later.
- ✅ ATR-multiple trailing-stop state machine in backtest, paper, and live (Phase 13 / ADR 0036).
- ✅ Phase 11 OOS holdout, walk-forward validation, and cross-market studies (ADR 0035). Parameter sweeps, WFO, and stitched OOS equity shipped as ADR 0044.
- ✅ Deterministic versioned `thytrader-buy-and-hold-v1` benchmark comparison derived from the reverified result, source run, and immutable dataset. It uses the same published taker fee, fixed slippage, and V1/V2 fill assumptions, reports return/drawdown/cost evidence, preserves V1/V2 canonical bytes, and is exposed as a separate read-only API/dashboard comparison. See [derived buy-and-hold benchmark](decisions/0011-derived-buy-and-hold-benchmark.md).

### Next delivery increment

The browser/API research loop is implemented: it recovers and saves validated drafts, publishes
immutable strategy evidence, presents a bounded semantic summary plus honest V1/V2/V3 support matrix,
archives a publication without mutating its evidence, launches explicit exact-version V1/V2/V3
research or a composed OOS/walk-forward/sweep/WFO study, and compares complete stored result histories across
versions.

Paper and live share one execution worker. Maker entries are implemented (`limit_limit_gtc` +
`post_only`); stops and time-exits are marketable sells. Live orders use Advanced Trade REST v3 JSON
with `RESTClient` only as signed HTTP. Phase 13 extras are shipped. Remaining deferred: operator
runbook drills; destination circuit breakers listed above.

**Exit gate met for the research slice:** reference-strategy results are deterministic, disclose
assumptions, resist lookahead, and pass adversarial fill/risk tests.

## Phase 4: Paper execution — ✅ Complete (narrow 1h|5m maker loop)

- Persistent simulated broker using normalized 1h or 5m candle-close events (live 5m arrived in Phase 13).
- Same published strategy semantics used by backtests/live trading.
- Continuous `thytrader-execution-worker` supervision.
- Deploy tab with pause/resume/stop, position, orders, fills, and reject reasons.

**Exit gate met:** a user can deploy one published strategy to paper mode; the worker evaluates each
closed 1h or 5m candle once, records intents/fills/position, and obeys pause and stop.

## Phase 5: Live maker execution — ✅ Narrow path complete; extras deferred

- Deploying `live` is the arming action; credentials required.
- Idempotent maker entries and ordinary take-profit orders via REST v3 JSON.
- Paginated fills as the fill ledger; GET-order after submit.
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
- ✅ Explicitly separated, confirmation-gated research mutation tools for drafts, immutable publication,
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
