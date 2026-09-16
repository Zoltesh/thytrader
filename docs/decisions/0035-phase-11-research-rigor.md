# 0035: Phase 11 walk-forward, OOS, and cross-market research studies

- Status: Accepted
- Date: 2026-09-15
- Relates to: [0007](0007-immutable-research-run-specifications.md),
  [0009](0009-deterministic-bar-level-backtest-engine.md),
  [0010](0010-constant-spread-backtest-provenance.md),
  [0011](0011-derived-buy-and-hold-benchmark.md),
  [0017](0017-maker-limit-bar-backtest.md),
  [0031](0031-coinbase-first-platform-end-state.md)

## Context

Shipped research submits one published strategy fingerprint against one verified dataset and one
evaluation window. That is necessary but not sufficient for research rigor. The canonical schema
already requires an untouched out-of-sample period and walk-forward validation before any
"profitable" label. ADR 0031 names historical and **cross-market** analysis as destination research.
Roadmap Phase 11 is that slice: walk-forward / OOS workflows, richer templates, and a clearer
engine-support matrix.

Parameter sweeps, grid search, and auto-tuning remain forbidden as authoring. A walk-forward study
must not fit a new parameter set on each in-sample window. Paper, live, 5m live, HTF execution,
on-demand trades, `1m`/`2h` clocks, journals, notify, and agent orchestration stay out of this
slice.

## Decision

Ship a versioned research-study contract `thytrader-research-study-v1` that **composes** existing
`thytrader-bar-backtest-v1` / `v2` / `v3` submissions. It does not add a fourth engine, mutate
canonical result bytes, or grant trading authority.

### Study kinds

| Kind | Meaning |
|---|---|
| `oos_holdout` | One frozen strategy and dataset. Split a half-open evaluation window into in-sample then out-of-sample, with an optional embargo of unused bars between them. |
| `walk_forward` | Same frozen fingerprint. Rolling or anchored folds of `in_sample_bars` then `out_of_sample_bars`, advancing by `step_bars`. |
| `cross_market` | Two to eight published strategy/dataset bindings on **distinct** USD spot products, same evaluation window, capital, costs, and engine. |

Each planned window becomes one ordinary backtest submission (idempotent by execution fingerprint).
The study document lists those child run/result fingerprints plus per-window summaries. Aggregate
OOS figures are equal-weight window statistics and concatenated trade counts. They are **not** a
stitched continuous equity curve.

Walk-forward is **validation**, not optimization: every fold uses the same published strategy
fingerprint. In-sample windows are reported so operators can see IS/OOS degradation. Honest
performance claims use OOS windows only.

### Persistence

Child backtests remain the append-only evidence. The study document is derived at plan/submit time
and is content-addressed. Repeating `submit-study` with identical assumptions reuses child results.
No new Alembic revision.

### Templates

`create-draft` accepts an explicit template id. `ema-trend` is the existing conservative reference
(default). Additional research templates: `rsi-mean-reversion`, `macd-trend`, and
`bollinger-mean-reversion`. Templates are starting drafts, not proven edges.

### Engine-support matrix

The matrix is a first-class read-only contract with **V1, V2, and V3** columns. V3 consumes
maker-only close-limit entries, wait/unfilled policy, and close-time exits. V2 alone consumes
constant-spread stress. Cooldown and trailing stops remain unsupported on every bar engine.

## Consequences

- Agents can submit OOS, walk-forward, and cross-market studies through `thytrader-research` with
  `--confirm`, without inventing private window math.
- Existing V1/V2/V3 result fingerprints stay unchanged.
- Cross-market still requires one published single-instrument strategy per product (Phase 10 did
  not widen the strategy document).
- Parameter sweeps, walk-forward optimization, and stitched OOS equity are research composition
  in [ADR 0044](0044-parameter-sweeps-wfo-stitched-equity.md). This ADR remains validation-only
  authoring: one published fingerprint per `walk_forward` fold.
- Paper/live HTF evaluation is [ADR 0041](0041-paper-live-htf-filter-evaluation.md).

## Alternatives considered

- **Walk-forward optimization (refit each IS window):** rejected; the schema forbids auto-tuning
  and a new fingerprint each fold would not be one published strategy.
- **Persist a study table (Alembic 0022):** deferred; child results already identify the experiment
  and a new migration is not required for this slice.
- **Widen one strategy document to many products:** rejected; that is a schema/runtime change, not
  research rigor.
- **Leave the UI matrix as V1/V2 only:** rejected; V3 is a shipped engine and omitting it is the
  honesty bug this slice fixes.
