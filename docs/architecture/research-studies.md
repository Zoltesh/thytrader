# Research studies (walk-forward, OOS, cross-market, sweeps, WFO)

## Purpose and boundary

`thytrader-research-study-v1` turns one typed study request into several ordinary bar-backtest
submissions. It is a research-only composition layer: it cannot create an order intent, connect to
Coinbase, or grant paper/live authority. Child evidence stays the existing immutable run and result
fingerprints. Every child uses the single backtest model (`engine: "thytrader-backtest"`,
[ADR 0083](../decisions/0083-unified-backtest-model.md)); studies do not reinterpret its fill
semantics. Plan and submit bodies take optional `spread_bps` and no engine-version field (sending
one returns 422). Study documents, summaries, and catalog rows carry no engine version.

The public commands are:

```bash
uv run thytrader-research list-templates
uv run thytrader-research backtest-model
uv run thytrader-research plan-study --file study.json
uv run thytrader-research submit-study --file study.json --confirm
uv run thytrader-research list-studies [--kind parameter_sweep]
uv run thytrader-research show-study --study-fingerprint sha256:…
uv run thytrader-research show-evidence --strategy-fingerprint sha256:…
uv run thytrader-research list-results [--limit 20] [--cursor CURSOR]
uv run thytrader-research list-strategies [--limit 50] [--cursor CURSOR]
uv run thytrader-research create-strategy --template rsi-mean-reversion --confirm
```

`plan-study`, `backtest-model`, `list-templates`, `list-studies`, `show-study`, `show-evidence`,
`list-results`, and `list-strategies` are read-only. Default `show-study` includes `window_pnl`
headlines without child equity curves. `show-evidence` separates in-sample, genuine out-of-sample,
parameter-sweep candidates, paper, and live — sweep means are never labeled OOS.
`list-results` / `list-strategies` pages are at most 100 rows with `has_more` / `next_cursor`.
`submit-study` requires `--confirm`. Repeating an identical submit reuses child backtests and is
idempotent in the study catalog when canonical bytes match.

Strategy authoring still represents one human-chosen parameter set. Sweeps and WFO are a separate
research activity that can manufacture overfit results
([ADR 0044](../decisions/0044-parameter-sweeps-wfo-stitched-equity.md),
[ADR 0052](../decisions/0052-richer-sweep-axes-study-catalog.md)). Honest claims use selected
out-of-sample windows and disclose stitched vs equal-weight aggregates.

## Study kinds

| Kind | Child windows |
|---|---|
| `oos_holdout` | One in-sample window and one out-of-sample window, optional `embargo_bars` unused between them. `oos_fraction` is the last share of the evaluation bars assigned to OOS. |
| `walk_forward` | For each fold, one IS window and one OOS window. `fold_mode` is `rolling` (IS start advances by `step_bars`) or `anchored` (IS start fixed; IS length grows by `step_bars`). Every window uses the snapshot of the request's `strategy_id` taken at submit. |
| `cross_market` | One full-window backtest per market binding. Products must be unique. Timeframes must match. Markets name `markets[].strategy_id`, or one top-level `strategy_id` plus `markets[].product_id` (server-derived per-market variants, below). |
| `parameter_sweep` | One full-window child per candidate on the shared evaluation bounds. Candidates come from **exactly one** of `candidate_strategy_ids` (2–64 valid strategies, each snapshotted at submit) or `parameter_axes` (1–4 axes, Cartesian product ≤ 64). More than 8 candidates run only as an async job (see Budgets). |
| `walk_forward_optimization` | Same fold geometry as `walk_forward`, but every candidate is simulated on every IS and OOS window. Selection uses only in-sample `selection_metric`. The matching OOS child is the fold claim. |

Evaluation bounds are half-open UTC candle boundaries, the same contract as a single backtest.
Walk-forward and WFO require at least one complete fold inside `[evaluation_start, evaluation_end)`.
At most 24 folds. Cross-market accepts 2–8 markets.

### Budgets

A study runs one of two ways ([ADR 0089](../decisions/0089-agent-research-ergonomics.md)):

| Mode | Candidates (sweep / WFO) | Child windows | How |
|---|---|---|---|
| Synchronous | ≤ 8 | ≤ 128 | `submit-study --confirm` (HTTP 201) runs every child inside the request |
| Async job | ≤ 64 | ≤ 512 | `submit-study --async --confirm` (HTTP 202) runs in the research worker |

A synchronous submit over budget is HTTP 422 `study_budget_exceeded` naming `--async`. An async
submit is planned (snapshots, derived candidates, dataset bounds, window budget) before it is
queued, so it fails with the same 422s instead of failing later in the worker. `plan-study` plans
against the async budget. Plans for studies above the synchronous budget carry a warning that they
run only as async jobs, and grids above 8 candidates also carry a data-snooping warning: searching
more candidates makes it likelier that the best in-sample result is luck, so honest claims stay on
selected out-of-sample windows. The ops contract advertises both budgets as `study_budgets`.

### Omitted datasets and bounds

Study starts may omit dataset fingerprints and both evaluation bounds. Each omitted fingerprint
binds the newest complete catalog dataset for that product and clock (decision clock, HTF filter,
extra indicator clocks) from the configured ingestion provider, exactly as `POST /api/v1/backtests`
does; a clock with no dataset is HTTP 422 `datasets_missing`. Omitted bounds become the common
covered window: the intersection of every child's default backtest window, taken over every market
of a cross-market study and every sweep/WFO candidate (derived candidates may need more warmup).
Plan, submit, and async 202 responses echo `bound_datasets` and the exact `evaluation_start` /
`evaluation_end`. These echo fields are outside the canonical plan and study documents and change no
fingerprint; the internal request carries the bound values, so `request_fingerprint` stays exact.

### Per-market variants

A cross-market start may name one top-level `strategy_id` plus `markets[].product_id` (2–8
distinct products) instead of one authored strategy per market. The server re-targets the base
definition's instrument at each product (rules, sizing, exits, and execution copied exactly; name
suffixed with the product; tag `research-market-variant`) and records each as a content-addressed
snapshot that keeps the base `strategy_id`, as sweep variants do, so variants are deleted with their
strategy. The base's own product reuses its snapshot. Multi-instrument strategies cannot be
re-targeted. Plan and submit both record variants (idempotently), and child windows name each
variant's `strategy_fingerprint`.

`walk_forward` validation does **not** retune parameters. WFO selects among candidate snapshots or
derived variant snapshots (which keep the base `strategy_id`); it does not peek at OOS to choose the winner.

### Parameter axes

Each axis has an optional `target` (`indicator` default, omitted from canonical JSON). Legal
parameters depend on the target:

| Target | Locator | Parameters |
|---|---|---|
| `indicator` | `indicator_id` | `period`, `fast_period`, `slow_period`, `signal_period`, `k_period`, `d_period`, `atr_period`, `tenkan_period`, `kijun_period`, `senkou_b_period`, `rsi_period`, `stoch_period`, `short_period`, `medium_period`, `long_period`, `annualization_periods`, `stdev_multiplier`, `multiplier`, `step`, `max_step`, `value`, and `offset` (the declaration's bar lag, [ADR 0086](../decisions/0086-indicator-catalog-expansion-and-offset.md)) |
| `sizing` | none | `risk_fraction`, `min_quote_notional`, `max_quote_notional` |
| `exits` | none | `initial_stop_multiple`, `take_profit_multiple`, `trailing_stop_multiple`, `max_bars_held` |
| `execution` | none | `max_entry_wait_bars` |
| `entry_literal` / `htf_literal` | `indicator_id`, optional `condition_operator` | `literal` |

Values are 2–8 unique strings per axis. The **Cartesian product** across axes is at most 64 total
candidates, and at most 8 for a synchronous submit (not 8 per axis). Product id and decision timeframe are not sweepable. Axes substitute the named field; they do not rewrite operators or
invent trailing stops. Derived definitions copy the base document, raise `warmup_bars` when new
periods require it, and keep the base `strategy_id` so their snapshots belong to that strategy. Indicator-only cells keep the
ADR 0044 fingerprint 3-tuple.

`plan-study` derives in memory and does not persist. It loads dataset manifests and
rejects windows that fail the same warmup / end-of-window liquidation-candle bound check as child
backtests (`422 study_window_rejected`, named field plus suggested ISO range).
`submit-study --confirm` snapshots every named strategy and any
derived variants, then submits ordinary backtests, then stores the assembled study in the catalog.
Requests name strategies with `strategy_id`, `candidate_strategy_ids`, and `markets[].strategy_id`
([ADR 0082](../decisions/0082-strategy-root-mutable-strategies-auto-snapshots.md)); the canonical
request and windows still carry the resolved snapshot `strategy_fingerprint` values.

`selection_metric` is `total_return_fraction` or `total_net_pnl` (maximize) or
`maximum_drawdown_fraction` (minimize). Ties break on the lexicographically smaller strategy
fingerprint. Empty candidate fields, the default metric, and default `target=indicator` are omitted
from canonical JSON so Phase 11 / ADR 0044 request fingerprints stay stable.

### Persisted catalog

`submit-study` writes one `published_research_studies` row (Alembic `0031`, ops contract
`thytrader-ops-contract-v19`). PostgreSQL is durable. The API without a database keeps a
process-local catalog. Operator `--local` without PostgreSQL reports `STUDY_CATALOG_UNAVAILABLE`
rather than an empty healthy list. Since Alembic `0048` each row carries the primary
`strategy_id` (FK, `ON DELETE CASCADE`) and `research_study_strategies` links every strategy the
study includes, so `GET /api/v1/research/studies?strategy_id=` (CLI `list-studies --strategy-id`)
lists every study that touches a strategy, and deleting any included strategy deletes the study.
`list-studies` returns newest-first summaries without child
equity. `GET /api/v1/research/studies/{study_fingerprint}` defaults to the same bounded summary
(`window_count`, aggregates, stitch metadata without `points`). Pass `?detail=full` for child
`windows`. `show-study` uses the default summary. `thytrader-operator studies` is the same catalog
without trading authority.

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

This is not a separate backtest model and does not claim live fill quality.

### Parameter-sweep aggregate naming

Parameter sweeps score `sweep_candidate` full windows on one shared evaluation range, so their
aggregates must not read as out-of-sample evidence. For a pure sweep (no in-sample windows), the
aggregate zeroes every `oos_*` field and populates `candidate_window_count`,
`candidate_trade_count`, `candidate_winning_trade_count`, `mean_candidate_return_fraction`, and
`mean_candidate_drawdown_fraction` instead. Only holdout, walk-forward, and WFO OOS windows use
`oos_*` names.

## HTTP

- `GET /api/v1/research/backtest-model` — the single model's `engine`, `decision_record`,
  `honesty`, and `assumptions[]` (`key`, `label`, `detail`).
- `GET /api/v1/research/templates` — draft template ids.
- `GET /api/v1/research/templates/{template_id}` — one template's `indicator_ids`, shipped
  `defaults`, warmup, and `sweepable_axes` (404 on unknown ids).
- `POST /api/v1/research/studies/plan` — window schedule, no simulation.
- `POST /api/v1/research/studies` — plan plus idempotent child submissions and catalog persist.
- `GET /api/v1/research/studies` — newest-first catalog rows (`kind`, `limit` ≤ 100, `offset` ≥ 0).
- `GET /api/v1/research/studies/{study_fingerprint}` — bounded study summary (`detail=summary`
  default). `?detail=full` returns child windows and stitched points.
- `POST /api/v1/backtests?async=true` — queue one long backtest (HTTP 202 + `job_id`).
- `GET /api/v1/backtests/jobs/{job_id}` — async backtest job status and fingerprints when complete.
- `GET /api/v1/operator/studies` — operator catalog report without child equity.

Study requests forward `htf_dataset_fingerprint` and `indicator_dataset_fingerprints` onto each
child backtest. Unbound extra indicator clocks still require those fingerprints; an extra TF that
equals `htf_filter.timeframe` stays on the HTF dataset.

## Explicitly not in this slice

- paper or live evaluation;
- auto-tuning inside `save-strategy` / strategy authoring;
- interpolating candles or equity across embargo gaps;
- carrying open positions across OOS windows in one engine run;
- rewriting WFO in-sample selection;
- extra exchanges or experiential ML.
