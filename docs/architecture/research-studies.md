# Research studies (walk-forward, OOS, cross-market)

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

## Study kinds

| Kind | Child windows |
|---|---|
| `oos_holdout` | One in-sample window and one out-of-sample window, optional `embargo_bars` unused between them. `oos_fraction` is the last share of the evaluation bars assigned to OOS. |
| `walk_forward` | For each fold, one IS window and one OOS window. `fold_mode` is `rolling` (IS start advances by `step_bars`) or `anchored` (IS start fixed; IS length grows by `step_bars`). |
| `cross_market` | One full-window backtest per market binding. Products must be unique. Timeframes must match. |

Evaluation bounds are half-open UTC candle boundaries, the same contract as a single backtest.
Walk-forward requires at least one complete fold inside `[evaluation_start, evaluation_end)`. At
most 24 folds. Cross-market accepts 2–8 markets.

Walk-forward does **not** retune parameters. Every window uses the request's published strategy
fingerprint (or, for cross-market, that market's fingerprint).

## Aggregate honesty

The study summary reports:

- per-window trade count, net PnL, return fraction, win rate, and max drawdown fraction;
- OOS-only concatenated trade/win counts and an equal-weight mean of OOS return and drawdown
  fractions;
- when IS windows exist, the equal-weight mean IS return and the IS−OOS return gap.

It does not stitch equity curves across windows, invent annualization, or claim live fill quality.
Overlapping OOS windows (`step_bars` < `out_of_sample_bars`) are allowed and disclosed.

## HTTP

- `GET /api/v1/research/engine-support` — V1/V2/V3 matrix.
- `GET /api/v1/research/templates` — draft template ids.
- `POST /api/v1/research/studies/plan` — window schedule, no simulation.
- `POST /api/v1/research/studies` — plan plus idempotent child submissions.

## Explicitly not in this slice

- parameter sweeps or walk-forward optimization;
- persisted study rows / a new Alembic revision;
- paper or live evaluation;
- multi-instrument strategy documents;
- interpolated candles.
