# 0083: One unified backtest model — no versioned engines

- Status: Accepted
- Date: 2026-09-30
- Supersedes in part: [0007](0007-immutable-research-run-specifications.md) (the
  `engine_contract_version`, `broker`, and `bar_execution` run-spec fields),
  [0008](0008-deterministic-signal-evaluation.md) (the request-only `thytrader-bar-v1` and
  signal-only `thytrader-bar-signal-v1` contracts), [0009](0009-deterministic-bar-level-backtest-engine.md)
  (the next-open taker engine `thytrader-bar-backtest-v1`), [0010](0010-constant-spread-backtest-provenance.md)
  (spread stress as a separate engine `thytrader-bar-backtest-v2`), [0017](0017-maker-limit-bar-backtest.md)
  (maker-limit semantics as a separately selectable `thytrader-bar-backtest-v3`),
  [0062](0062-research-paper-semantics-audit-stage-4.md) (the `thytrader-bar-backtest-v4` identity
  and "prefer v4" guidance; its semantics are kept), [0066](0066-research-ops-contract-v4.md)
  (advertising engines in `backtest_engines`), [0019](0019-ops-contract-identity.md) (the
  `backtest_engines` ops-contract field), [0035](0035-phase-11-research-rigor.md) (the engine-support
  matrix and per-study engine choice), and [0081](0081-live-chrome-portfolio-bot-detail-trade.md)
  (the Test run bar's engine default from `engine-support`).
- Relates to: [0011](0011-derived-buy-and-hold-benchmark.md), [0044](0044-parameter-sweeps-wfo-stitched-equity.md),
  [0056](0056-multi-instrument-documents-and-pyramiding.md), [0077](0077-derived-performance-metrics.md),
  [0082](0082-strategy-root-mutable-strategies-auto-snapshots.md)

## Context

Research accumulated four parallel bar-backtest engines, each an identity-bearing contract so
that old result bytes never changed meaning: next-open taker fills (V1), V1 plus constant spread
stress (V2), resting maker limits with a non-causal terminal bar (V3), and the corrected causal
maker model (V4, ADR 0062). Two more contracts existed only to support them: a request-only run
(`thytrader-bar-v1`) and a signal-trace-only run (`thytrader-bar-signal-v1`).

Only V4 matches how paper and live trade: they rest a post-only entry at the signal close, wait,
cancel or reprice, stop on the fill bar, and match resting take-profits before managing the
position. V1–V3 produced numbers that paper and live could not reproduce, the engine picker made
operators choose between models they could not evaluate, and the spread stress that operators
did want lived only on the least realistic engine. Migration 0048 (ADR 0082) already wiped every
stored backtest, so no historical evidence depends on the old identities.

## Decision

**One model.** There is exactly one backtest simulator. It keeps the V4 corrected maker-limit
semantics documented in [backtest simulation](../architecture/backtest-simulation.md): resting
post-only entries at the signal close (maker fee, no slippage, touch-through fill), the entry
wait with cancel/reprice, a stop-only fill bar, resting take-profit matched before the stop on
later bars (amended 2026-10-01: the stop is now checked before the take-profit on every bar, in
backtests and paper; see the amendment below), bar-extreme stops at `min(open, stop)` with taker fee and fixed slippage, ATR
trailing after the stop check, taker time exits at the close, taker liquidation at the open of
the `evaluation.ends_at` bar with no other processing there, shorts, multi-instrument shared
cash in `product_id` order, pyramiding, the `decimal64-half-even-v1` context, deterministic
ordering, and `validity_limits` on every summary. The V1, V2, and V3 code paths are deleted.
The equity curve now always records every evaluation close plus one terminal point at
`evaluation.ends_at` (V4 used to overwrite the last close when a position was open).

**Unversioned identity.** Run specifications, signal traces, results, benchmarks, and derived
metrics carry `engine: "thytrader-backtest"` in place of `engine_contract_version`. The field
stays inside canonical bytes so every fingerprint still binds the simulator that produced it.
There is no version suffix because there is no second engine to distinguish; a future semantic
change requires a superseding ADR **and** a new identity string so existing fingerprints never
acquire new meaning. The run spec drops the `broker` and `bar_execution` blocks: with one model
they had exactly one legal value each, and `engine` plus this ADR already name them.

**Spread stress is an optional assumption, not an engine.** `costs.spread_bps` (decimal string,
default `"0"`, at most 1000) is a disclosed constant total bid-ask spread stress. It composes
with the maker model because it touches only the legs a spread actually affects: marketable
(taker) exits cross half the spread before fixed slippage, stop triggers compare the stressed
bid (ask for shorts) with the bar extreme, and open positions mark at the stressed bid (ask for
shorts). Resting maker entries and take-profits keep filling at their posted limit — a resting
limit does not pay the spread. Zero stress produces byte-identical results to omitting the
field. Spread-stressed taker fills record `reference_price`, `executable_side`, and
`spread_cost`, and the summary records `total_spread_cost`.

**No engine selection anywhere.** `POST /api/v1/backtests`, study plan/submit bodies, the
research CLIs, the playbook, and the operator-chat tools accept no engine field. A request that
still sends `engine_contract_version` is **rejected** (HTTP 422, CLI exit) with the message
"engine_contract_version was removed: ThyTrader has one backtest model (ADR 0083)…". Rejecting
rather than ignoring was chosen because a caller that still names `v1` or `v2` expects
semantics it will not get; a silent ignore would hand it different numbers. `GET
/api/v1/research/engine-support` is removed; `GET /api/v1/research/backtest-model` (and
`thytrader-research backtest-model`) describes the single model's assumptions instead. The UI
drops the engine picker and every V-label and shows a "How backtests simulate" disclosure.

**Ops contract and storage.** The ops contract becomes `thytrader-ops-contract-v42` with
`backtest_engine: "thytrader-backtest"` replacing `backtest_engines`, and expected schema
revision `0049`. Migration 0049 deletes the research rows written by retired engines
(`research_jobs`, study membership, studies, results, run specs) and drops
`published_research_studies.engine_contract_version`. Strategies, snapshots, dataset
bindings, and deployments are untouched.

**No regressions of naming.** `tests/test_no_versioned_backtest_engines.py` fails when a retired
engine id, a standalone `V1`–`V4` label, or the engine-support matrix reappears in `web/src`,
`skills/`, or `docs/user`. Naming the removed `engine_contract_version` field to say it is
rejected stays allowed, because operator agents need that migration hint.

## Consequences

- Backtests, studies, walk-forward selection, and paper/live comparison all use the model that
  paper and live actually trade. Operators no longer choose an engine.
- Spread-stress sweeps (0 / 10 / 25 / 50 bps) now run on the realistic model instead of the
  next-open taker approximation.
- Results written before 0049 cannot be loaded; they were test data. Callers that still send an
  engine selector get an explicit error and must drop the field.
- Validity limits stay disclosed on every result: maker touch-fill optimism (candles do not show
  queue position) and the resting-target-before-stop ordering on bars after the fill bar.
- The buy-and-hold benchmark uses unified taker semantics for both legs (taker fee, fixed
  slippage, half the spread stress) and marks at the stressed bid.

## Alternatives considered

- **Keep V1–V4 selectable.** Rejected: only V4 matches paper/live, and the choice misled more
  than it informed.
- **Rename V4 to `thytrader-backtest-v1`.** Rejected by the operator: any version suffix implies
  alternatives that do not exist.
- **Drop the engine field entirely.** Rejected: the field keeps result fingerprints bound to the
  simulator identity, so a future semantic change cannot collide with today's bytes.
- **Drop spread stress.** Rejected: it composes cleanly with maker semantics when limited to
  taker legs, triggers, and marks, and operators use it to reject strategies that only work
  frictionlessly.
- **Ignore a stale `engine_contract_version` instead of rejecting it.** Rejected: silently
  returning maker-model numbers to a caller that asked for next-open fills is worse than a clear
  422.

## Amendment (2026-10-01): stop before take-profit on every bar

When one candle touches both the working stop and a resting take-profit, the candle cannot show
which traded first. Resolving it as the take-profit flattered results. Both candle simulations
(backtest and paper) now resolve it as the **stop**. The disclosed validity code is renamed
`tp_before_stop_same_bar` → `stop_before_tp_same_bar`. Live is unaffected: Coinbase's OCO
bracket decides what actually fills. The fill-bar rule is unchanged (only the stop is eligible).

