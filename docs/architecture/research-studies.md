# Research studies (walk-forward, OOS, cross-market, sweeps, WFO)

## Purpose and boundary

`thytrader-research-study-v1` turns one typed study request into several ordinary bar-backtest
submissions. It is a research-only composition layer: it cannot create an order intent, connect to
Coinbase, or grant paper/live authority. Child evidence stays the existing immutable run and result
fingerprints. This contract does not reinterpret V1, V2, or V3 fill semantics.

The public commands are:

```bash
uv run thytrader-research list-templates
uv run thytrader-research engine-support
uv run thytrader-research plan-study --file study.json
uv run thytrader-research submit-study --file study.json --confirm
uv run thytrader-research create-draft --template rsi-mean-reversion --confirm
```

`plan-study` and `engine-support` / `list-templates` are read-only. `submit-study` requires
`--confirm`. Repeating an identical submit reuses child backtests.

Strategy authoring still represents one human-chosen parameter set. Sweeps and WFO are a separate
research activity that can manufacture overfit results
([ADR 0044](../decisions/0044-parameter-sweeps-wfo-stitched-equity.md)). Honest claims use selected
out-of-sample windows and disclose stitched vs equal-weight aggregates.

## Study kinds

| Kind | Child windows |
|---|---|
| `oos_holdout` | One in-sample window and one out-of-sample window, optional `embargo_bars` unused between them. `oos_fraction` is the last share of the evaluation bars assigned to OOS. |
| `walk_forward` | For each fold, one IS window and one OOS window. `fold_mode` is `rolling` (IS start advances by `step_bars`) or `anchored` (IS start fixed; IS length grows by `step_bars`). Every window uses the request's published strategy fingerprint. |
| `cross_market` | One full-window backtest per market binding. Products must be unique. Timeframes must match. |
| `parameter_sweep` | One full-window child per candidate on the shared evaluation bounds. Candidates come from **exactly one** of `candidate_strategy_fingerprints` (2–8 published) or `parameter_axes` (1–4 axes, Cartesian product ≤ 8). |
| `walk_forward_optimization` | Same fold geometry as `walk_forward`, but every candidate is simulated on every IS and OOS window. Selection uses only in-sample `selection_metric`. The matching OOS child is the fold claim. |

Evaluation bounds are half-open UTC candle boundaries, the same contract as a single backtest.
Walk-forward and WFO require at least one complete fold inside `[evaluation_start, evaluation_end)`.
At most 24 folds and 128 child windows. Cross-market accepts 2–8 markets.

`walk_forward` validation does **not** retune parameters. WFO selects among published or
submit-published derived fingerprints; it does not peek at OOS to choose the winner.

### Parameter axes

Each axis names an existing indicator id and a legal parameter (`period`, `fast_period`,
`slow_period`, `signal_period`, `stdev_multiplier`, or `value`) with 2–8 unique values. Axes do not
rewrite entry/exit logic. Derived definitions copy the base document, substitute those parameters,
raise `warmup_bars` when the new periods require it, and take a deterministic UUIDv7 `strategy_id`.
`plan-study` derives in memory and does not persist. `submit-study --confirm` publishes missing
derived documents through the existing publication store, then submits ordinary backtests.

`selection_metric` is `total_return_fraction` or `total_net_pnl` (maximize) or
`maximum_drawdown_fraction` (minimize). Ties break on the lexicographically smaller strategy
fingerprint. Empty candidate fields and the default metric are omitted from canonical JSON so
Phase 11 request fingerprints stay stable.

## Aggregate honesty

The study summary reports:

- per-window trade count, net PnL, return fraction, win rate, and max drawdown fraction;
- OOS-only concatenated trade/win counts and an equal-weight mean of OOS return and drawdown
  fractions (WFO uses **selected** OOS only; parameter-sweep aggregates are the equal-weight mean
  of candidate windows and are not an OOS claim);
- when IS windows exist, the equal-weight mean IS return and the IS−OOS return gap.

Overlapping OOS windows (`step_bars` < `out_of_sample_bars`) are allowed and disclosed. They are
not independent.

### Stitched OOS equity

When two or more scored OOS windows are time-ordered and non-overlapping, the study document may
include a **derived** `stitched_oos_equity` curve. Each child still starts from
`initial_quote_balance` and liquidates at its window end. Stitching compounds per-window equity
**returns** in chronological order. Embargo gaps are not interpolated. Overlapping OOS refuses
stitching and keeps the overlap warning.

Stitching applies to `walk_forward` validation OOS windows and to **selected** WFO OOS windows. It
does not apply to parameter sweeps (same window, different fingerprints) or to a single OOS holdout
window. Summaries stay when the point series exceeds 4096 marks.

This is not a fourth backtest engine and does not claim live fill quality.

## HTTP

- `GET /api/v1/research/engine-support` — V1/V2/V3 matrix.
- `GET /api/v1/research/templates` — draft template ids.
- `POST /api/v1/research/studies/plan` — window schedule, no simulation.
- `POST /api/v1/research/studies` — plan plus idempotent child submissions.

Study requests forward `htf_dataset_fingerprint` and `indicator_dataset_fingerprints` onto each
child backtest. Unbound extra indicator clocks still require those fingerprints; an extra TF that
equals `htf_filter.timeframe` stays on the HTF dataset.

## Explicitly not in this slice

- persisted study rows / a new Alembic revision;
- paper or live evaluation;
- auto-tuning inside `save-draft` / strategy authoring;
- interpolating candles or equity across embargo gaps;
- carrying open positions across OOS windows in one engine run;
- extra exchanges, shorting, or attached entry brackets;
- an ops-contract bump (research composition only).
