---
name: thytrader-research
description: >-
  Create, edit, clone, import, and delete ThyTrader strategies (one mutable
  object per strategy, revision-guarded saves) and submit or compare
  deterministic backtests and composed research studies by strategy id through
  the confirmation-gated thytrader-research CLI. Use when the user asks to
  create or change a strategy, delete strategies, run a backtest, or run an OOS /
  walk-forward / cross-market / parameter-sweep / WFO study, or to list persisted
  study catalog rows. Requires explicit --confirm for every mutation.
  Never deploys, paper-trades, live-trades, arms, or cancels orders.
---

# ThyTrader research

Bounded research mutations only. This skill is not an extension of `thytrader-operator` and has no paper, live, arming, cancellation, or kill-switch authority.

Default transport is the loopback HTTP API. The CLI resolves its base URL from `--base-url`, then
`THYTRADER_API_BASE_URL`, then the `THYTRADER_API_HOST` / `THYTRADER_API_PORT` settings (the same
`.env` Compose reads; the default port is `8200`, but installs may override it). Do not hard-code a
port; for raw `curl`, export `THYTRADER_API_BASE_URL` and use `"$THYTRADER_API_BASE_URL/api/v1/..."`.
Pass `--local` only when you intentionally want PostgreSQL stores. Do not fall back from HTTP to
the database if the API is down.

Failures name what failed. An API rejection prints `HTTP <status> <detail.code>: <detail.message>`
(for example `HTTP 422 study_window_rejected: … Suggested range: …`); act on the message instead of
retrying blindly. Transport failures say which call timed out, that the API is unreachable (and how
the base URL was resolved), or that the API closed the connection before answering (retry a read;
check state before repeating a mutation). A 5xx adds what to do next. Unreadable `--file` paths,
invalid JSON, and schema mismatches are named too.

Production installs enforce the application trust boundary
([ADR 0061](../../docs/decisions/0061-application-trust-boundary.md)): HTTP mutations need
`Authorization: Bearer <installation-token>` from `THYTRADER_INSTALLATION_TOKEN` or
`$THYTRADER_CREDENTIALS_DIR/.installation-token` ([ADR 0070](../../docs/decisions/0070-mutation-cli-installation-auth.md)).
The CLI sends that header automatically; `--local` bypasses HTTP and therefore the boundary.

HTTP contracts behind this CLI ([ADR 0082](../../docs/decisions/0082-strategy-root-mutable-strategies-auto-snapshots.md)):
`GET/POST /api/v1/strategies`, `GET/PUT/DELETE /api/v1/strategies/{strategy_id}`,
`POST /api/v1/strategies/bulk-delete`, `POST /api/v1/strategies/{strategy_id}/clone`,
`POST /api/v1/strategies/import`, `GET /api/v1/strategies/snapshots/{strategy_fingerprint}`,
`POST /api/v1/backtests`, `POST /api/v1/research/studies`, `GET /api/v1/research/studies`,
`GET /api/v1/research/jobs?strategy_id=`. The agent-facing mutation path is
`uv run thytrader-research` with `--confirm`.

## Strategy model (no drafts, no publish, no versions)

- A strategy is **one mutable object** identified by `strategy_id` (UUIDv7) with an
  optimistic-concurrency `revision`. You edit it and save it in place. There is no draft,
  publish, revise, archive, or version number.
- Saving an **invalid** work-in-progress document is allowed: the response carries
  `validation: {valid, issues:[{loc, message}]}` and `current_fingerprint: null`. Backtest,
  study, and deployment starts require a currently valid definition and fail closed with HTTP
  422 `strategy_invalid` (the `issues` list says what to fix).
- **Read `validation.valid` after every write.** `create-strategy`, `save-strategy`,
  `import-strategy`, `clone-strategy`, and `show-strategy` all nest the result the same way:
  `validation: {valid, issues, warnings}` ([ADR 0094](../../docs/decisions/0094-research-honesty-and-agent-ergonomics.md)).
  The top-level `valid` / `issues` / `warnings` copies on write commands are deprecated and kept
  for one release only. When the result is invalid the CLI also prints one line on stderr, for
  example `thytrader-research: imported as an INVALID draft (2 issues):
  entry.when.all[0].left.input: unknown field "input". …`; an invalid import is not usable.
- Issue `loc` values are **document paths** (`entry.when.all[0].left.input`,
  `exits.take_profit.multiple`), never validator internals, and messages are plain
  (`unknown field "input"`, `"multiple" is required`, `must be an indicator operand (needs
  "indicator") or a literal operand (needs "literal")`). Fix the field at that path. Drafts saved
  before ADR 0094 keep their old paths until they are saved again.
- Valid documents may also carry advisory `validation.warnings:[{code, loc, message}]`
  ([ADR 0090](../../docs/decisions/0090-research-correctness-optional-take-profit-diagnostics.md)).
  `short_target_may_be_non_positive` means a short's reward/risk target
  (`entry − stop_multiple × ATR × take_profit.multiple`) reaches zero at plausible volatility, so
  those entries will be skipped as `target_not_positive`; `long_stop_may_be_non_positive` is the
  same for a long's ATR stop. Warnings never block a save, backtest, or deployment. Report them to
  the operator; lower the multiples or set `exits.take_profit` to `{"kind": "none"}` only when asked.
- `exits.take_profit` is `{"kind": "reward_risk", "multiple": "2"}` or `{"kind": "none"}` (no
  target: exit on the stop, the optional ATR trail, the time exit, or the signal exit). `none` has
  no `multiple`; `take_profit_multiple` sweep axes fail on it.
- Optional `exits.signal_exit` is `{"when": <condition tree>}`
  ([ADR 0093](../../docs/decisions/0093-signal-based-exits.md)): the same `all`/`any`/`not` grammar,
  operand rules, depth 4 / 64 nodes, and decision-list indicators as `entry.when` (never an
  `htf_filter` indicator; the HTF filter gates entries only). Example "hold while fast > slow":

  ```json
  "signal_exit": {"when": {"all": [{"left": {"indicator": "fast"},
    "operator": "crosses_below", "right": {"indicator": "slow"}}]}}
  ```

  It is checked on every closed bar **after the fill bar** while a position is open; a match sells
  as a taker at that bar's close (like the time exit). The `initial_stop` stays mandatory and still
  protects the position: the stop wins a same-bar tie, a take-profit the bar touched wins, and the
  trailing stop and time exit still apply (first trigger wins; the signal exit names an exit due on
  the same close as the time exit). Unknown indicators, a missing or extra `series`, a crossover
  against a literal, or extra keys are rejected exactly like `entry.when`. Omitting `signal_exit`
  (or sending `null`) keeps the document's canonical bytes and fingerprint unchanged; adding or
  editing it changes the fingerprint, so results bind the exact exit rule.
- Starting a backtest, study, or deployment **snapshots** the current definition automatically:
  canonical JSON addressed by `strategy_fingerprint` (`sha256:` + 64 hex), deduplicated. Results,
  studies, jobs, and bots record `strategy_id` plus that snapshot `strategy_fingerprint`, so they
  are always exact about the rules they used. A row whose `strategy_fingerprint` differs from the
  strategy's `current_fingerprint` ran on an **earlier edit**; read it with `show-snapshot`.
- Canonical documents no longer contain `version` or `status`. Legacy files that still carry them
  import fine; the keys are discarded and never fingerprinted.
- Saves are revision-guarded: pass the `revision` you last read. A stale save returns HTTP 409
  `strategy_revision_conflict` with `current_revision`; re-read with `show-strategy`, reapply the
  change, and save again. Never overwrite blindly.

In-app operator chat (`/chat`, `/api/v1/operator-chat`) may invoke these same HTTP routes. It is
not extra authority: mutations still need in-app confirmation. Do not treat chat as this skill.
Browser strategy, backtest, and study writes establish a CSRF session automatically and send its
matching header and cookie. If a browser write gets HTTP 401 `CSRF token required for browser
mutations`, diagnose the browser/proxy path; do not change strategy inputs or disable the security
boundary. The CLI uses installation Bearer auth without browser CSRF.

## Hard stop

When operating a running instance, do not edit `src/`, `compose.yaml`, Dockerfiles, Alembic, or tests.
Do not search the tree for a code patch. Report failures through this skill. Every HTTP command
preflights the full `/health/ready` ops contract. Rebuild or restart only with `make run` when the
user asked, or when the CLI reports a version or ops-contract mismatch, or HTTP 404 on an agent
route while `/health/ready` is 200 (the shared stale-image signal). Matching `0.1.0` alone is not
current-image evidence. Open the `ops/` workspace instead of
the git root. Run every `uv run thytrader-*` command from the repository root (the parent of `ops/`).

## How backtests simulate (one model)

There is **one** backtest model, `engine: "thytrader-backtest"`
([ADR 0083](../../docs/decisions/0083-unified-backtest-model.md)). There is no engine to pick and
no engine field to send: a backtest or study body that includes `engine_contract_version` is
rejected with HTTP 422 ("engine_contract_version was removed…"). Drop the field and resubmit.
`uv run thytrader-research backtest-model` (or `GET /api/v1/research/backtest-model`) prints the
assumptions. Full semantics: `docs/architecture/backtest-simulation.md`.

- **Maker-limit entries rest.** A matched completed-candle signal rests a post-only limit at that
  close. A later candle fills it only if its low (high for shorts) trades through, at the limit,
  maker fee, no slippage. It rests up to `execution.max_entry_wait_bars` candles, then cancels or
  reprices per `on_unfilled_entry`. Entries are post-only maker limits everywhere (backtest,
  paper, live): `execution.entry_preference` must be `maker_only`. A save with the retired
  `marketable_limit` is stored invalid with an issue at `execution.entry_preference` and cannot be
  backtested or deployed until it is changed to `maker_only`.
- **No silent skips.** A matched signal that cannot rest an entry is counted with its reason
  (cooldown, max positions, a resting entry, an open position, or geometry/sizing such as
  `target_not_positive`, `stop_not_positive`, `notional_below_minimum`, `insufficient_cash`).
  `show-result` returns those counts as `diagnostics` (see below).
- **Stops and targets on bar extremes.** On the fill candle only the stop can trigger (stop-first).
  From the next candle the take-profit rests. The stop is always checked first: a candle that
  touches both the stop and the take-profit is resolved as the stop (conservative; paper does the
  same). Otherwise a touched take-profit fills at the target (maker). Stops fill as takers at the stop or the worse
  gapped open; ATR trailing ratchets after the check. A matched `exits.signal_exit` rule (never
  on the fill candle) and the time exit sell at the close as takers, signal first.
- **End of window.** Open inventory sells at the open of the `evaluation_end` candle; nothing else
  happens on that candle.
- **Costs.** Maker fee on resting entries/take-profits; taker fee plus `fixed_slippage_bps` on stop,
  time, signal, and end exits. Optional `spread_bps` (default 0, max 1000) is a constant total spread
  **stress**: taker exits cross half of it, stops trigger and positions mark on the stressed
  bid/ask, maker fills stay at the limit. Compare the same strategy at 0 / 10 / 25 / 50 bps.
- **Honesty.** Candles do not show queue position: a touched limit is assumed to fill fully.
  Every summary lists `validity_limits` (`maker_touch_full_fill`, `stop_before_tp_same_bar`,
  `spot_short_synthetic` for shorts, and `signal_exit_at_close` when the strategy declares
  `exits.signal_exit`: the backtest prices the exit at the signal bar's own close, which paper and
  live can only approach by selling right after it); read them before any deployment claim.
  Trade exit reasons are `stop_loss`, `take_profit`, `time_exit`, `signal`, and `evaluation_end`. Backtests are
  simulated research evidence, never paper or live fills.

## Commands

| Need | Command |
|---|---|
| List the strategy library | `uv run thytrader-research list-strategies [--tag TAG] [--limit 50] [--cursor CURSOR]` |
| Show one strategy (document, validation, revision, current fingerprint) | `uv run thytrader-research show-strategy --strategy-id UUID` |
| Show one snapshot a result or bot used | `uv run thytrader-research show-snapshot --strategy-fingerprint sha256:…` |
| Create a strategy from a template | `uv run thytrader-research create-strategy [--template rsi-mean-reversion] [--product-id ETH-USD] [--timeframe 5m] [--experiential-model-id UUID] --confirm` |
| Save (edit) a strategy in place | `uv run thytrader-research save-strategy --strategy-id UUID --file document.json --revision N --confirm` |
| Import a JSON document as a new strategy | `uv run thytrader-research import-strategy --file document.json --confirm` |
| Clone a strategy into a new identity (optionally named) | `uv run thytrader-research clone-strategy --strategy-id UUID [--name "EMA ETH-USDC 1d"] --confirm` |
| Delete one strategy (hard delete) | `uv run thytrader-research delete-strategy --strategy-id UUID --confirm` |
| Preview a bulk delete | `uv run thytrader-research bulk-delete-strategies --strategy-id UUID [--strategy-id UUID …] --dry-run` |
| Bulk delete strategies | `uv run thytrader-research bulk-delete-strategies --strategy-id UUID [--strategy-id UUID …] --confirm` |
| Preview deleting every strategy with one tag | `uv run thytrader-research bulk-delete-strategies --tag TAG --dry-run` |
| Delete every strategy with one tag | `uv run thytrader-research bulk-delete-strategies --tag TAG --confirm` |
| List strategy templates | `uv run thytrader-research list-templates` |
| Show one template's defaults and sweepable axes | `uv run thytrader-research show-template --template macd-trend` |
| Describe the backtest model's fill and cost assumptions | `uv run thytrader-research backtest-model` |
| Submit an idempotent backtest | `uv run thytrader-research submit-backtest --file request.json --confirm` |
| Queue a long backtest (HTTP 202) | `uv run thytrader-research submit-backtest --file request.json --async --confirm` |
| Poll one async backtest job | `uv run thytrader-research show-backtest-job --job-id UUID` |
| Plan OOS / walk-forward / cross-market / sweep / WFO windows | `uv run thytrader-research plan-study --file study.json` |
| Submit a composed research study | `uv run thytrader-research submit-study --file study.json --confirm` |
| Queue a long composed study (HTTP 202) | `uv run thytrader-research submit-study --file study.json --async --confirm` |
| Poll one async research job | `uv run thytrader-research show-research-job --job-id UUID` |
| List one strategy's research jobs (sync and async, newest first) | `uv run thytrader-research list-research-jobs --strategy-id UUID [--limit 20]` |
| Read back a study after an ambiguous submit | `uv run thytrader-research find-study-by-request --request-fingerprint sha256:…` |
| Cancel one queued or running research job | `uv run thytrader-research cancel-research-job --job-id UUID --confirm` |
| List persisted study catalog rows | `uv run thytrader-research list-studies [--kind parameter_sweep] [--strategy-id UUID] [--limit 50]` |
| Show one persisted study summary | `uv run thytrader-research show-study --study-fingerprint sha256:…` |
| List result summaries | `uv run thytrader-research list-results [--strategy-id UUID \| --strategy-fingerprint sha256:…] [--limit 20] [--cursor CURSOR]` |
| Show one result summary | `uv run thytrader-research show-result --result-fingerprint sha256:…` |
| Trace the entry rule bar by bar for one result | `uv run thytrader-research-evaluate sha256:… [--outcome matched] [--limit 200] [--cursor CURSOR] [--pretty]` |
| Show IS/OOS/sweep/paper/live evidence | `uv run thytrader-research show-evidence --strategy-fingerprint sha256:…` |

`list-results`, `show-result`, `show-strategy`, `show-snapshot`, `show-evidence`, `list-templates`, `show-template`, `backtest-model`, `plan-study`,
`list-studies`, `list-strategies`, `list-research-jobs`, `show-research-job`, and
`show-study` are read-only and
do not use `--confirm`. `list-results` and `list-strategies` page at most 100 rows (`has_more` /
`next_cursor`). One `show-study` call explains a whole study
([ADR 0094](../../docs/decisions/0094-research-honesty-and-agent-ergonomics.md)): every
`window_pnl` row carries `label`, `role`, `fold_index`, `product_id`, `evaluation_start`,
`evaluation_end`, `strategy_fingerprint`, its candidate's `axis_values` (for example
`{"fast.period": 20, "slow.period": 200}`; `{}` when the study has one candidate),
`total_net_pnl`, `total_return_fraction`, `trade_count`, and `selected`. `candidates[]` sums every
window per candidate: `axis_values`, `selected_window_count`, `in_sample_window_count` /
`in_sample_total_net_pnl`, `oos_window_count` / `oos_total_net_pnl` / `oos_positive_window_count` /
`oos_trade_count` (every WFO fold scores every candidate out of sample, so robustness across the
grid is visible, not only the selected path), and `full_window_count` /
`full_window_total_net_pnl` (sweep candidates and cross-market legs: full-range windows, not an
out-of-sample claim). `stitched_oos_equity.points` holds the stitched path thinned to at most 200
marks (first, last, and each bucket's low and high); `point_count` is the full count and
`stitched_oos_points_downsampled` says whether marks were dropped. `?detail=full` still returns
`windows[]` and every stitched mark. No `show-snapshot` or `plan-study` calls are needed to read a
WFO. Queued research jobs report
`progress_total >= 1` (0/1 means not started, not 0/0). Sequential `create-strategy` loops
can exceed a 180s agent timeout after HTTP 201 — list-strategies before retrying; the mutation is
already persisted. `submit-study` requires `--confirm`. Studies compose ordinary
unified-model backtests. In-sample-only studies expose `oos_window_count=0` and absent OOS means; do not treat
`mean_is_return_fraction` as out-of-sample evidence. `walk_forward` validation freezes one snapshot of the strategy. `parameter_sweep` and
`walk_forward_optimization` select among `candidate_strategy_ids` (each snapshotted at submit) or
`parameter_axes` (derived variants keep the base `strategy_id`, so they belong to that strategy). Axes default
to target `indicator` with any parameter the indicator declares — `period`, `fast_period`, `slow_period`,
`signal_period`, `k_period`, `d_period`, `atr_period`, `tenkan_period`, `kijun_period`, `senkou_b_period`,
`rsi_period`, `stoch_period`, `short_period`, `medium_period`, `long_period`, `annualization_periods`,
`stdev_multiplier`, `multiplier`, `step`, `max_step`, `value` — or `offset` (the declaration's bar lag;
`0` means the current bar; `constant` rejects it). Derived variants re-derive warmup, including offsets.
Optional `target` may be `sizing` (`risk_fraction`, `min_quote_notional`,
`max_quote_notional`), `exits` (`initial_stop_multiple`, `take_profit_multiple`,
`trailing_stop_multiple`, `max_bars_held`), `execution` (`max_entry_wait_bars`), or
`entry_literal` / `htf_literal` (`literal`, optional `condition_operator`). The Cartesian product is
at most 64 total candidates (≤8 values per axis does not imply a small total). A synchronous
`submit-study --confirm` allows at most 8 candidates and 128 child windows; a larger grid (9–64
candidates) or schedule (up to 512 child windows) needs `submit-study --async --confirm`, and a
synchronous attempt returns HTTP 422 `study_budget_exceeded` naming `--async`. `plan-study` plans
against the async budget and warns when a study is async-only. Searching more candidates raises the
chance that the best in-sample result is luck: grids above 8 carry a data-snooping warning, so judge
selected out-of-sample windows (WFO), never the best candidate's in-sample or sweep mean.
`show-template` prints one template's `indicator_ids`, shipped `defaults`, warmup, and
`sweepable_axes` so parameter-axis studies can be authored without reading source or guessing ids.
Product and timeframe are not sweepable. Selection uses only in-sample `selection_metric`; it does
not look ahead from OOS.
`plan-study` derives axis candidates in memory, then rejects windows that cannot fit the selected dataset after warmup and the reserved end-of-window bar (open inventory liquidates at its open). A rejected plan is HTTP 422 `study_window_rejected` and names the field that failed (`evaluation_start` or `evaluation_end`) plus the same suggested ISO range child backtests use. It returns a compact plan summary by default
(`window_count`, `fold_count`, fingerprints, warnings). Pass `?detail=full` on the HTTP route when
child windows are required. `submit-study --confirm` snapshots every named strategy and any derived axis
variants, then submits ordinary backtests, then persists a catalog row. Equivalent effective plans dedupe through
`plan_fingerprint` even when request bounds differ. Long WFO batches should use
`submit-study --async --confirm` and poll `show-research-job`. If a synchronous `submit-study`
fails with a timeout or unreachable-API error, the study may already be persisted: the CLI error
names the `request_fingerprint` and a readback command. Re-run
`thytrader-research find-study-by-request --request-fingerprint sha256:…` before retrying the
submission; resubmission is idempotent. `list-studies` is newest-first
summaries. `GET /api/v1/research/studies/{study_fingerprint}` defaults to the same bounded summary
(`window_count`, aggregates, per-row axis values and bounds, `candidates[]`, and a thinned
stitched path). Pass `?detail=full` for child `windows`. `show-study` uses the default summary. Operator
`thytrader-operator studies` is the same catalog. Durable storage is PostgreSQL; `--local` without
a database is unavailable, not empty. Stitched OOS equity compounds non-overlapping window
returns for `walk_forward` OOS and selected WFO OOS; overlapping OOS and embargo gaps are not
interpolated. Parameter-sweep aggregates are not an out-of-sample claim: sweep documents carry
`candidate_window_count` / `mean_candidate_return_fraction` (their `oos_*` fields are zero/absent),
and only holdout, walk-forward, and WFO OOS windows use `oos_*` names. Cross-market studies
need 2–8 distinct products, named one of two ways (never mixed): `markets[].strategy_id` for
strategies already authored per market, or one top-level `strategy_id` plus
`markets[].product_id` (for example `{"kind": "cross_market", "strategy_id": "…", "markets":
[{"product_id": "BTC-USDC"}, {"product_id": "ETH-USDC"}]}`). The second form needs no clones: the
server re-targets the base strategy at each product and records each variant as an exact snapshot
that keeps the base `strategy_id` (like sweep variants, tagged `research-market-variant`); the base's
own product reuses its snapshot. Child windows report each variant's `strategy_fingerprint`.
Multi-instrument strategies cannot be re-targeted (clone them per market). See
[`docs/architecture/research-studies.md`](../../docs/architecture/research-studies.md).

`create-strategy` defaults to template `ema-trend`, `BTC-USDC` / `1h`. Pass `--template`
(`ema-trend`, `rsi-mean-reversion`, `macd-trend`, `bollinger-mean-reversion`, `donchian-breakout`,
`supertrend-trend`, `squeeze-breakout`, `zscore-mean-reversion`, `ema-trend-hold`), `--product-id`, and
`--timeframe` (any ingested venue clock) for another USD, USDC, or USDT spot product. Paper and live start by `strategy_id` through `thytrader-runtime`; the server snapshots the current definition.
`ema-trend-hold` is the trend-holding template ([ADR 0093](../../docs/decisions/0093-signal-based-exits.md)):
long when EMA(20) (`fast`) crosses above EMA(100) (`slow`), `exits.signal_exit` sells when `fast`
crosses back below `slow`, a 3× ATR initial stop, no take-profit, a wide 5× ATR trail (optional:
set `trailing_stop` to `{"enabled": false}` to rely on the cross alone), and a 1000-bar time cap.
`show-template --template ema-trend-hold` lists its defaults and sweepable axes; a `fast`/`slow`
`period` axis moves the entry and the exit rule together because both reference those ids.
`show-result` (HTTP and `--local`) and operator `performance` copy the snapshot's
`instrument.quote_currency` into the result `currency` field; USDC-product results report
`currency: USDC`. They also include the derived `thytrader-performance-metrics-v1` block
(`sharpe`, `sortino`, `calmar`, `sqn`, `cagr`, annualized volatility, max consecutive losses,
exposure fraction, mark-to-mark buy-and-hold) without changing canonical result fingerprints.
The quote is `null` when the snapshot cannot be loaded; never relabel an unverified result as USD or USDC.
Optional `--experiential-model-id` (HTTP only; `--local` refuses) loads
`GET /api/v1/memory/models/{id}` fail-closed and merges `experiential_advisory` into the
create-strategy JSON. It does not change strategy semantics, place orders, or arm live
trading. Train models with `skills/thytrader-memory/SKILL.md`.
Optional `htf_filter` (ADR 0025) is a higher-timeframe closed-bar filter AND-ed with LTF entry.
`create-strategy` does not add it. `save-strategy` JSON may include the block. A backtest of such a
strategy runs on an HTF dataset (`htf_dataset_fingerprint`, distinct from `dataset_fingerprint`);
send it only when the strategy declares `htf_filter`. Extra indicator clocks that are not already
`htf_filter.timeframe` run on `indicator_dataset_fingerprints` (`[{timeframe, dataset_fingerprint}, …]`
ordered by increasing duration, each distinct from LTF and HTF). Ingest every clock with
`skills/thytrader-data/SKILL.md` first. Multi-instrument documents run on
`additional_instrument_datasets`: one `{product_id, dataset_fingerprint,
htf_dataset_fingerprint?, indicator_dataset_fingerprints?}` per extra covered product, ordered by
`product_id`. Each extra product needs a complete dataset on the decision clock plus the HTF and
extra-TF clocks the document declares. Identities must be unique and distinct from the primary
LTF/HTF/extra-TF fingerprints. `dataset_fingerprint` remains the primary instrument. A fingerprint
whose product or timeframe the document does not cover is refused with HTTP 422
`backtest_window_rejected` naming that dataset and the covered products/timeframes (async jobs fail
with the same message). Fix the binding; do not retry. All of these
fingerprints are optional: see **Datasets bind automatically** below. Backtests evaluate
last-completed extra-TF and HTF bars only. Paper and live evaluate the same last-completed bars on
live complete-only candles; they do not bind frozen extra-TF or HTF fingerprints.

Discover implemented indicator kinds with `uv run thytrader-operator indicators` before authoring.
It lists all 53 kinds with `category`, `inputs` / `input_mode`, every parameter's bounds, builder
`default`, and `help`, ordering `constraints`, output series, the `warmup` formula, and
`default_warmup_bars` ([ADR 0086](../../docs/decisions/0086-indicator-catalog-expansion-and-offset.md)).
Shipped kinds:
- Trend: `ema`, `sma`, `wma`, `dema`, `tema`, `hma`, `kama`, `vwma` (close/volume), `supertrend`
  (high/low/close; series `value`/`direction`, 1 up / −1 down), `parabolic_sar` (high/low; `step`,
  `max_step`), `aroon` (high/low; `up`/`down`/`oscillator`), `ichimoku` (high/low;
  `tenkan`/`kijun`/`senkou_a`/`senkou_b`, no forward displacement, no chikou), `vortex`
  (`plus`/`minus`), `linear_regression` (`value`/`slope`), `trix` (close), `adx`
  (`adx`/`plus_di`/`minus_di`; warmup `2 * period - 1`).
- Momentum: `rsi`, `roc`, `momentum`, `stochastic` (`k`/`d`), `williams_r`, `cci`, `macd`
  (`macd`/`signal`/`histogram`, fast < slow), `ppo` (`ppo`/`signal`/`histogram`), `stochastic_rsi`
  (`k`/`d`), `ultimate_oscillator`, `awesome_oscillator` (high/low), `cmo`, `tsi` (`tsi`/`signal`).
- Volatility: `atr`, `natr`, `stdev` (population), `stdev_sample` (`N-1`), `bollinger`
  (`middle`/`upper`/`lower`), `bollinger_percent_b`, `bollinger_bandwidth`, `keltner` and `donchian`
  (`upper`/`middle`/`lower`), `choppiness`, `historical_volatility` (percent; optional
  `annualization_periods`).
- Volume: `volume_sma`, `mfi`, `obv` (`obv`/`signal`), `accumulation_distribution` (`ad`/`signal`),
  `cmf`, `vwap` (rolling window), `force_index`.
- Statistical: `zscore`, `percent_rank`. Price: `identity` (one of open/high/low/close/volume, empty
  parameters), `constant` (`parameters.value`, no input), `highest`, `lowest`.
Configurable kinds accept one of open/high/low/close/volume; locked kinds take exactly the listed
`inputs` array. OBV and A/D levels depend on where the series starts; compare them with their
`signal` series. Single-output operands omit `series`. Multi-series operands must name one declared
series. Any declaration except `constant` may add `offset` (0–500): it reads the value from that
many completed bars earlier on the indicator's own clock and adds `offset` to warmup — for example
`{"kind": "donchian", "input": ["high", "low"], "parameters": {"period": 20}, "offset": 1}` is the
previous bar's channel for a breakout. To compare an indicator with its own earlier value, declare it
twice (once with `offset`). Do not invent unlisted kinds or pass through a TA library.
Optional per-indicator `timeframe` on LTF-list indicators must be a coarser integer-multiple venue
clock; omit it to keep the decision clock. `constant` and HTF-filter indicators omit `timeframe`.
`crosses_above` / `crosses_below` need two indicator operands. Compare an indicator to a
level with `greater_than*` / `less_than*` and a `literal`, or declare a `constant` kind and cross that
id. Copy a candle field with `identity`. `import-strategy --file … --confirm` always creates a new
strategy (the server mints a fresh `strategy_id` and `created_at`; `version`/`status` keys are
discarded). `save-strategy --strategy-id UUID --file … --revision N --confirm` replaces the whole
document of an existing strategy (first save after create/import/clone uses `--revision 1`; each
accepted save bumps revision; the server forces the row's `strategy_id`/`created_at`). A save is
accepted even when the document is invalid — read `validation.valid` and `validation.issues` in the
output; do not treat a saved invalid document as ready to backtest. HTTP 422
`strategy_document_invalid` means the file is not a JSON object or exceeds 256 KiB. HTTP 422 that says
`engine_contract_version` is required (rather than "was removed") is a stale Compose image —
rebuild with `make run`. A current `/health/ready` ops contract advertises
`backtest_engine: "thytrader-backtest"` ([ADR 0083](../../docs/decisions/0083-unified-backtest-model.md)).

`submit-backtest` JSON names the strategy with `strategy_id` (not a fingerprint); the server
snapshots the current definition and returns `strategy_id` plus the snapshot `strategy_fingerprint`
with `run_fingerprint` / `result_fingerprint` and `bound_datasets` (async 202 returns `job_id` plus
the same strategy identities and `bound_datasets`). HTTP 404 `strategy_not_found`; HTTP 422
`strategy_invalid` lists `issues`. `submit-study` / `plan-study` JSON uses `strategy_id`,
`candidate_strategy_ids`, and `markets[].strategy_id` (or `markets[].product_id`) the same way;
study windows still report each snapshot `strategy_fingerprint`.

**Datasets bind automatically** ([ADR 0089](../../docs/decisions/0089-agent-research-ergonomics.md)).
`dataset_fingerprint`, `htf_dataset_fingerprint`, `indicator_dataset_fingerprints`,
`additional_instrument_datasets` (backtests), and per-market dataset fields (cross-market studies)
are all optional. For each clock the strategy needs — decision clock, HTF filter, extra indicator
clocks, and each additional instrument — an omitted fingerprint binds the newest complete dataset
the catalog lists for that product and timeframe from the configured ingestion provider (`coinbase`
with credentials, otherwise `demo`; the same rows as `thytrader-operator data-catalog`). Explicit
fingerprints are used exactly as sent, and you may mix the two (for example send only
`dataset_fingerprint` and let the HTF clock bind). Every response echoes `bound_datasets`:
`[{product_id, timeframe, role: decision|filter|indicator, dataset_fingerprint, source:
request|latest_catalog}]`, and the bound fingerprints are part of the run's identity, so record them
with the result. A clock with no cataloged dataset fails closed with HTTP 422 `datasets_missing`:
`detail.missing` lists each `{product_id, timeframe, role}` and the message names the exact
`uv run thytrader-data watch-add … --confirm` and `ingest … --confirm` commands; nothing runs.

**Study bounds may be omitted.** `plan-study` / `submit-study` may omit both `evaluation_start` and
`evaluation_end` (never just one). The server then uses the common covered window: the intersection
of every child backtest's default window (each market of a cross-market study, and every sweep/WFO
candidate, whose derived warmup may be longer). Plan, submit, and async 202 responses echo
`evaluation_start` / `evaluation_end` and `bound_datasets`; the request fingerprint covers the filled
bounds. An async submit is planned before it is queued, so an oversized or infeasible study returns
422 immediately instead of failing in the worker.

`submit-backtest` may omit both `evaluation_start` and `evaluation_end`. The server fills the
common covered intersection of the LTF dataset and every bound extra clock (HTF filter dataset,
unbound indicator-timeframe datasets, and additional-instrument datasets). LTF warmup still sits
before the start and one LTF bar after the end is reserved for the end-of-window liquidation at its
open. Extra clocks use last-completed coverage only (no extra-clock terminal bar). If that intersection is empty, or
supplied dates do not fit, the API returns 422 with a suggested ISO range. `evaluation_end` uses a
half-open interval `[evaluation_start, evaluation_end)`; the latest allowed `evaluation_end` named
in the error is inclusive. Do not invent a window that the catalog cannot cover. For 1m or other
long runs that exceed gateway timeouts, pass `--async` (or `POST /api/v1/backtests?async=true`) and
poll `show-backtest-job` / `GET /api/v1/backtests/jobs/{job_id}` until `completed` or `failed`.
A synchronous submit is a queued job the API waits on for up to
`THYTRADER_RESEARCH_SYNC_WAIT_SECONDS` (default 25 s; the CLI allows 60 s). When the research
worker has not finished by then it prints the job with `sync_wait_seconds` and `next_action` instead
of the result (see "Where research runs"); poll it, do not resubmit. If the CLI itself prints
`Timed out after … waiting for the ThyTrader API`, the backtest may still be running: check
`list-research-jobs` / `list-results` before submitting again, or re-run with `--async`
([ADR 0085](../../docs/decisions/0085-fast-research-ingest.md)).
Required assumptions: `initial_quote_balance`, `maker_fee_rate`, `taker_fee_rate`,
`fixed_slippage_bps`; optional `spread_bps` stress. Never send `engine_contract_version`.
Decimal fields (these costs, study `oos_fraction`, and `parameter_axes[].values`) accept JSON
numbers as well as strings: `"fixed_slippage_bps": 5` is the same request, the same request and
execution fingerprints, and the same run as `"5"`. Numbers are read through their shortest decimal
text, so send a string when more than 15 significant digits matter. Booleans, `NaN`, and
infinities are rejected (HTTP 422).

**Every result states its window** ([ADR 0094](../../docs/decisions/0094-research-honesty-and-agent-ergonomics.md)).
`show-result` (and `GET /api/v1/backtests/{fp}`, summary and full, and operator `performance`)
returns `window`: `{timeframe, evaluation_start, evaluation_end, first_evaluated_bar,
last_evaluated_bar, evaluation_bars, warmup_bars, warmup_start}`; `list-results` rows carry
`evaluation_start`, `evaluation_end`, `warmup_bars`, `evaluation_bars`, and
`total_return_fraction`. `evaluation_end` is exclusive (its bar's open liquidates what is held).
The window is derived from the run, outside the fingerprinted result bytes, so fingerprints are
unchanged; it is `null` when the run cannot be read. **When you compare strategies, pin
`evaluation_start` and `evaluation_end`.** Omitted bounds start after each strategy's own warmup:
on the same BTC-USDC 1d dataset, buy-and-hold read 48% for a strategy with a 60-bar warmup and 129%
for one with 110, only because the windows differed. Results whose `evaluation_start` /
`evaluation_end` differ are not comparable.

`show-result` returns the result summary, `window`, derived `metrics`, the published `costs`
(including `spread_bps`), and `diagnostics` — the entry funnel
`thytrader-backtest-diagnostics-v1` (`signals_matched`, `entries_rested`, `entries_filled`,
`entries_expired`, `entries_repriced`, `entries_refused_at_fill`, `entries_unfilled_at_end`,
`entries_size_capped`, `warmup_bars`, `skipped[{reason, count}]`, and `exit_reasons[{reason,
count}]`: closed trades per exit reason (`stop_loss`, `take_profit`, `time_exit`, `signal`,
`evaluation_end`), summing to the trade count; `null` on diagnostics recorded before ADR 0093).
Use it to explain few or zero trades before changing rules: `signals_matched` equals
`entries_rested` plus every skipped count. `entries_refused_at_fill` counts only fills that shared cash could no longer fund
(multi-instrument books); a cash-capped single book always funds. `diagnostics` is `null` for
results published before ADR 0090. Re-running the same backtest records it; a request whose
result predates the 2026-10-02 fill amendment (ADR 0083) re-simulates as a new result.

`thytrader-research-evaluate <result_fingerprint>` (a `run_fingerprint` of a completed backtest
also works) asks the API to re-evaluate that result's run and prints one bounded page of the
entry-condition trace: per-bar `indicator_values` and `entry_condition`
(`matched` / `not_matched` / `undefined`), plus `exit_condition` (same values) on every bar when the
strategy declares `exits.signal_exit` (whether a position was open to act on it is the simulator's
concern), outcome `counts` (of `entry_condition`), `total_records`, and `next_cursor`
(`GET /api/v1/backtests/{result_fingerprint}/signal-trace`). It is read-only, needs no
`--confirm`, and fails with the API's reason (for example `signal_trace_unavailable` when the
re-evaluated trace does not reproduce the result) instead of a generic message. It does not need
`THYTRADER_DATABASE_URL` or local Parquet files. It copies the snapshot's decision clock (`1m` through `1d`, including `2h` and
`4h`) into the compact summary `timeframe`. It does not default every result to `1h`.

## Maker/taker rates

`GET /api/v1/fees` (or `thytrader-operator fees`) includes `suggested_maker_fee_rate` /
`suggested_taker_fee_rate` when Coinbase credentials are present. They are the **account's own
reported Coinbase rates** (`suggestion_source=coinbase_account`; for example 0.005 / 0.009 on an
Intro tier) — what live fills are billed at
([ADR 0090](../../docs/decisions/0090-research-correctness-optional-take-profit-diagnostics.md)).
`schedule_maker_fee_rate` / `schedule_taker_fee_rate` with `suggestion_schedule_tier_id` and
`suggestion_schedule_version` are the pinned public band for the same volume, **context only**:
never copy them as defaults. Copy `suggested_*` into `submit-backtest` / `submit-study` JSON (and
pass them as `--maker-fee-rate` / `--taker-fee-rate` on paper starts) unless the operator supplied
custom rates.
Demo or missing credentials set `suggestion_source=unavailable` — do **not** use dashboard demo
`maker_fee_rate` / `taker_fee_rate` as research defaults, and do not invent a tier. The request must
still include explicit rates; submitted runs fingerprint those values. They are modeled
`CostAssumptions`, not observed Coinbase fills. Resting entries and take-profits use the **maker**
rate; stop, time, and end-of-window exits use the **taker** rate. Paper deploy accepts optional `maker_fee_rate` / `taker_fee_rate`
through `thytrader-runtime` ([ADR 0048](../../docs/decisions/0048-paper-deploy-fee-fields.md));
omitted paper rates keep the documented `0.001` maker / `0.002` taker schedule. Those paper rates
are also modeled assumptions, not observed Coinbase fills. Live Coinbase fees stay venue-recorded.

## Where research runs (research worker queue)

Every backtest, study, and portfolio backtest runs in the `research-worker` service, never in the
API ([ADR 0092](../../docs/decisions/0092-research-worker-pool.md)). The API validates, plans, and
queues; the pool runs at most `THYTRADER_RESEARCH_WORKER_COUNT` jobs at once (default 2), oldest
first, so heavy research no longer slows other API calls.

- **`queued` means waiting for a free research worker.** It is neither stuck nor failed. Read the
  queue with `uv run thytrader-operator health`: `payload.research_workers.queue` has `queued`,
  `running`, and `oldest_queued_age_seconds` (also split into `research_jobs` and
  `portfolio_backtests`), beside `configured_workers`, `live_workers`, and `workers[]` (`state`,
  `job_id`, `job_kind`, `jobs_completed`, `rss_bytes`, `heartbeat_age_seconds`). Queue position is
  roughly `queued` jobs older than yours divided by the worker count.
- The `research_worker` health component is `READY` when every worker heartbeats.
  `RESEARCH_WORKER_MISSING` or `RESEARCH_WORKER_STALE` mean queued research will not start until the
  service runs again (`make run`); report that instead of resubmitting. `RESEARCH_WORKER_PARTIAL`
  means some slots are down; the live ones keep draining the queue.
- **`running`** means one worker holds the job under a renewed lease; `progress_current` /
  `progress_total` advance per child window.
- **A synchronous submit is a job too.** `submit-backtest` / `submit-study` without `--async` waits
  up to `THYTRADER_RESEARCH_SYNC_WAIT_SECONDS` (25 s): a finished job prints the usual result (HTTP
  201, same 422s for rejected windows or budgets); otherwise the CLI prints `job_id`, `status`,
  `sync_wait_seconds`, and `next_action` (HTTP 202). The job keeps running: poll
  `show-research-job --job-id …`. Never resubmit a job that is still `queued` or `running`.
- **`attempts`** counts claims. A worker that crashes or is OOM-killed loses its lease; the job goes
  back to `queued` and runs again, at most 3 attempts in total.
- **`error_code`** on a failed job: `backtest_window_rejected`, `study_window_rejected`, or
  `study_budget_exceeded` (fix the request); `research_unavailable` (storage or worker outage,
  retry later); `research_worker_lost` (the job killed its worker on every attempt, so shrink it).
- `cancel-research-job --confirm` cancels a queued job at once; a running study stops before its
  next child window; a running single backtest finishes its current simulation first.

## Failed study submissions

- A failed synchronous `submit-study` prints the real underlying reason plus the request
  fingerprint (never a bare safe-failure line). Keep that identity for `find-study-by-request`.
- A definitive rejection (HTTP 4xx except 408) names the request identity and the reason but never
  claims an ambiguous submit state — nothing was persisted; fix the request instead of re-reading.
- Ambiguous failures (timeout, unreachable API, HTTP 408/5xx) keep the readback suffix: the study
  may already be persisted. Run `find-study-by-request --request-fingerprint …` before retrying.
- Failed async study jobs expose `failed_phase` (`plan`, `publish_derived` (snapshotting derived variants), `submit_children`,
  `persist_study`, or `unknown`), `failed_detail` (underlying cause text), and `error_message` in
  `show-research-job` output. Child-window 422s copy the rejection text into `failed_detail`.
  Decide retries from the phase, not from `progress_current`.

## Library and deletion

- `list-strategies` lists every strategy, newest-updated first, with `revision`, `valid`,
  `tags` (the document's `metadata.tags`), `current_fingerprint`, newest `backtest`, `paper_live`
  status, and `active_deployment_count`. `--tag TAG` (HTTP `GET /api/v1/strategies?tag=`) keeps
  only strategies whose `metadata.tags` include `TAG`; `total` and `next_cursor` then cover the
  matches, so pass the same `--tag` with `--cursor`. Tag the strategies you create in bulk (for
  example `per-market`) so you can list and clean them up later. Cross-market and sweep variants
  are snapshots of their base strategy, never library rows: they do not appear in
  `list-strategies` (not even under `--tag research-market-variant`).
- `clone-strategy --strategy-id UUID --name "…" --confirm` (HTTP `POST
  /api/v1/strategies/{id}/clone` with `{"name": "…"}`) names the copy in the same call; without
  `--name` it is `<name> (copy)`. To mirror one strategy across markets for a study, prefer
  `markets[].product_id` (no clones at all).
  The browser workspace is `/strategies/{strategy_id}` (Build · Test · Run · Why). Old `?version=`
  and fingerprint deep links resolve to the owning strategy through
  `GET /api/v1/strategies/snapshots/{strategy_fingerprint}`.
- `delete-strategy --strategy-id UUID --confirm` **hard-deletes** the strategy and everything that
  belongs to it: snapshots, backtests, run specs, studies that include it, research jobs, dataset
  bindings, and PAPER deployments with their orders, fills, positions, intents, and trade reasons.
  It also removes the strategy's portfolio sleeves; each removal is journaled in its portfolio and
  counted as `portfolio_sleeves`.
  It is refused with HTTP 409 `strategy_has_active_deployments` (with `deployment_ids`) while any
  bot of the strategy is running or paused — stop it with `skills/thytrader-runtime/SKILL.md`
  first (that is a runtime-lane action; this skill never stops bots). Stopped LIVE deployments are
  **kept** with their orders, fills, positions, trade reasons, and the snapshot they ran; they are
  detached (`strategy_id: null`, `strategy_deleted: true`, `strategy_name` kept). Real-money
  records are never destroyed. If the active risk policy allocated capital to the strategy, the
  same transaction publishes the next risk-policy version without that allocation
  (`risk_policy_republished: true`). The output `counts` lists what was removed.
- `bulk-delete-strategies --strategy-id … --dry-run` previews each id (`would_delete` with
  `counts`, `blocked`, or `not_found`) without writing; run it first and show the user the list.
  `--confirm` executes and returns one result per id (`deleted`, `blocked`, `not_found`, `failed`);
  partial success is normal — report each id's outcome. At most 100 ids per call.
- `bulk-delete-strategies --tag TAG --dry-run` previews every strategy tagged `TAG` (the CLI pages
  the tagged library, then sends batches of 100 to the same bulk route); `--tag TAG --confirm`
  deletes them. The output adds `tag` and `matched` and sums the per-batch counts. Safety is the
  same as by id: running or paused bots block their strategy (`blocked`) and stopped live books are
  kept. `--tag` and `--strategy-id` cannot be combined.
- Deletion cannot be undone. Only delete when the user explicitly named the strategies.

## Confirmation

- Never run `create-strategy`, `import-strategy`, `save-strategy`, `clone-strategy`, `delete-strategy`, `bulk-delete-strategies` (without `--dry-run`), `submit-backtest`, `submit-study`, or `cancel-research-job` unless the user explicitly asked for that mutation **and** `--confirm` is present, unless the user explicitly asked to operate under YOLO **and** operator `configuration` / `thytrader-playbook status` shows the `research` tier enabled.
- `--local` research always requires `--confirm` (YOLO is HTTP-only).
- If `--confirm` is missing in Safe mode, the CLI exits without writing. Do not retry with `--confirm` unless the user asked you to.
- Successful mutations print JSON identities (`strategy_id`, `strategy_fingerprint`, `run_fingerprint`, `result_fingerprint`, `study_fingerprint`). Keep those identities.

## Forbidden

- Deployments, pause/resume/stop, Coinbase orders, risk-limit edits, kill switches
- Direct PostgreSQL access as the public agent contract
- Treating a backtest as a live or paper fill
- Deleting strategies the user did not explicitly name, or deleting to "clean up" on your own initiative
- Editing application source to change strategy or backtest semantics on a running instance

Diagnose a running instance with `skills/thytrader-operator/SKILL.md` first when health is unknown. Coverage and ingest are `skills/thytrader-data/SKILL.md`. Paper/live control is `skills/thytrader-runtime/SKILL.md`. Strategy `timeframe` may be any ingested venue clock for backtests, paper, and live. A strategy's `htf_filter` is executable in paper and live.
