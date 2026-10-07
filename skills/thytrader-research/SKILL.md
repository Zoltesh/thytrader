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

Default transport is the loopback HTTP API (base URL from `--base-url`, `THYTRADER_API_BASE_URL`,
or settings; see the [shared rules](../README.md#shared-rules-every-lane)). Pass `--local` only when you intentionally want PostgreSQL stores. Do not fall back from HTTP to
the database if the API is down.

Failures name what failed. An API rejection prints `HTTP <status> <detail.code>: <detail.message>`
(for example `HTTP 422 study_window_rejected: … Suggested range: …`); act on the message instead of
retrying blindly. Transport failures say which call timed out, that the API is unreachable (and how
the base URL was resolved), or that the API closed the connection before answering (retry a read;
check state before repeating a mutation). A 5xx adds what to do next. Unreadable `--file` paths,
invalid JSON, and schema mismatches are named too.

HTTP mutations carry installation Bearer auth automatically
([ADR 0061](../../docs/decisions/0061-application-trust-boundary.md),
[ADR 0070](../../docs/decisions/0070-mutation-cli-installation-auth.md)); `--local` bypasses HTTP
and therefore the boundary.

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
- Optional `data_requirements.reference_instruments` declares up to **3 read-only reference
  instruments** ([ADR 0096](../../docs/decisions/0096-reference-instruments.md)), for example
  "trade alts only while BTC-USDC 1d close > EMA(100)":

  ```json
  "data_requirements": {"warmup_bars": 50, "required_fields": ["open","high","low","close","volume"],
    "reference_instruments": [{"id": "btc", "product_id": "BTC-USDC", "timeframe": "1d"}]},
  "indicators": [ …,
    {"id": "btc_close", "kind": "identity", "input": "close", "source": "btc", "parameters": {}},
    {"id": "btc_ema", "kind": "ema", "input": "close", "source": "btc", "parameters": {"period": 100}}]
  ```

  An indicator with `"source": "<id>"` reads that reference's bars on the reference's timeframe
  (it must omit `timeframe`; `constant` cannot take a source); operands in `entry.when` and
  `exits.signal_exit` use it like any indicator. Rules: `id` matches `^[a-z][a-z0-9_]{0,31}$` and
  is unique; no repeated `product_id`+`timeframe`; the reference uses the **strategy's quote
  currency**; its timeframe equals the strategy timeframe or is a coarser integer multiple (like an
  HTF clock); every reference is read by at least one indicator; ATR stop/trail indicators and
  `htf_filter` indicators never take a source (gate on a reference in `entry.when`). Warmup per
  reference is derived from its indicators (period plus offset), so `warmup_bars` covers only the
  traded instrument. **Alignment:** at each decision close only the last reference bar that had
  already closed is visible (an in-progress reference bar is never read; a same-timeframe reference
  reads the bar that closes with the decision bar). A reference is never traded: there are no
  cross-instrument orders and a strategy still trades one instrument (or its ADR 0056 covered
  products, which all share the same references). Omitting the list keeps canonical bytes and
  fingerprints; declaring one changes the fingerprint.
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

When operating a running instance, do not edit `src/`, `compose.yaml`, Dockerfiles, Alembic, or
tests, and do not search the tree for a code patch. Report failures through this skill. Rebuild
only with `make run` when the user asked or the [stale-image rule](../README.md#shared-rules-every-lane)
applies. Run every `uv run thytrader-*` command from the repository root (the parent of `ops/`).

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
  live can only approach by selling right after it, and `synthetic_no_trade_bars` when the
  evaluation window holds flat zero-volume bars for intervals without trades: thin markets'
  quiet bars, which volume indicators read as undefined;
  [ADR 0095](../../docs/decisions/0095-sparse-markets-no-trade-bars-listing-floors.md)); read them
  before any deployment claim.
  Trade exit reasons are `stop_loss`, `take_profit`, `time_exit`, `signal`, and `evaluation_end`. Backtests are
  simulated research evidence, never paper or live fills.

## Commands

| Need | Command |
|---|---|
| List the strategy library | `uv run thytrader-research list-strategies [--origin all\|operator\|research] [--tag TAG] [--limit 50] [--cursor CURSOR]` |
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
| Queue a long composed study (HTTP 202) | `uv run thytrader-research submit-study --file study.json --async --confirm` (waits up to 30 s for the 202; `--submit-timeout-seconds N` overrides) |
| Poll one async research job | `uv run thytrader-research show-research-job --job-id UUID` |
| List one strategy's research jobs (sync and async, newest first) | `uv run thytrader-research list-research-jobs --strategy-id UUID [--limit 20]` |
| Read back a study after an ambiguous submit | `uv run thytrader-research find-study-by-request --request-fingerprint sha256:…` |
| Cancel one queued or running research job | `uv run thytrader-research cancel-research-job --job-id UUID --confirm` |
| List persisted study catalog rows | `uv run thytrader-research list-studies [--kind parameter_sweep] [--strategy-id UUID] [--limit 50]` |
| Show one persisted study summary | `uv run thytrader-research show-study --study-fingerprint sha256:…` |
| List result summaries | `uv run thytrader-research list-results [--strategy-id UUID \| --strategy-fingerprint sha256:…] [--limit 20] [--cursor CURSOR]` |
| Show one result summary | `uv run thytrader-research show-result --result-fingerprint sha256:…` |
| Trace the entry rule bar by bar for one result | `uv run thytrader-research-evaluate sha256:… [--outcome matched] [--limit 200] [--cursor CURSOR] [--pretty]` |
| Explain one result bar by bar, including recorded fills | `uv run thytrader-research explain-bars --result-fingerprint sha256:… [--limit 100] [--cursor CURSOR]` |
| Show IS/OOS/sweep/paper/live evidence | `uv run thytrader-research show-evidence --strategy-fingerprint sha256:…` |

`list-results`, `show-result`, `explain-bars`, `show-strategy`, `show-snapshot`, `show-evidence`, `list-templates`, `show-template`, `backtest-model`, `plan-study`,
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
`submit-study --async --confirm` and poll `show-research-job`. The API pins strategy snapshots, datasets, and evaluation bounds before answering 202; the
research worker plans and validates async studies (ADR 0103). Acceptance does not mean the plan
is feasible: poll `show-research-job` and inspect `failed_phase: plan`, `error_code`, and
`failed_detail` on failure. Use `plan-study` for an explicit preflight. An async submit waits
up to 30 s for the acceptance (a
synchronous one 60 s); pass `--submit-timeout-seconds N` (1-300) to change the wait
([ADR 0097](../../docs/decisions/0097-runtime-parity-and-observability.md)). If any `submit-study`
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
`supertrend-trend`, `squeeze-breakout`, `zscore-mean-reversion`, `ema-trend-hold`, `btc-regime-gate`), `--product-id`, and
`--timeframe` (any ingested venue clock) for another USD, USDC, or USDT spot product. Paper and live start by `strategy_id` through `thytrader-runtime`; the server snapshots the current definition.
`ema-trend-hold` is the trend-holding template ([ADR 0093](../../docs/decisions/0093-signal-based-exits.md)):
long when EMA(20) (`fast`) crosses above EMA(100) (`slow`), `exits.signal_exit` sells when `fast`
crosses back below `slow`, a 3× ATR initial stop, no take-profit, a wide 5× ATR trail (optional:
set `trailing_stop` to `{"enabled": false}` to rely on the cross alone), and a 1000-bar time cap.
`show-template --template ema-trend-hold` lists its defaults and sweepable axes; a `fast`/`slow`
`period` axis moves the entry and the exit rule together because both reference those ids.
`btc-regime-gate` ([ADR 0096](../../docs/decisions/0096-reference-instruments.md)) goes long when
EMA(20) crosses above EMA(50) on the traded product, only while `btc_close > btc_ema` on the
read-only `BTC-<instrument quote>` 1d reference (EMA(100)), with an ATR stop and target. Its
`btc_ema` `period` axis needs no warmup edit (reference warmup is derived). Backtests need a
complete BTC 1d dataset in the same quote; it binds automatically like any other clock.
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
previous bar's channel for a breakout. An indicator operand may independently add a strict integer
`offset` (0–500), for example `{"indicator":"bands","series":"upper","offset":1}`.
It reads completed bars on that indicator's own clock before decision-clock alignment and adds
to any declaration offset. Use one declaration for current and prior reads so parameter sweeps
keep both synchronized. Read the strategy `summary` returned by show/create/save/import/clone
to check each entry and signal-exit operand's combined lag: declaration offset 2 plus operand
offset 3 appears as `(5 bars ago)`. Unlagged reads omit this suffix, and reference labels retain
their clock, for example `BTC · SMA(2) (3 bars ago) [1d]`. Literals reject offsets;
`constant` rejects positive offsets. Zero is omitted from canonical JSON. Include the largest
operand lag in warmup, including signal exits
and HTF-filter rules. The server derives extra-clock/reference warmup and validates supplied
decision/filter warmup. Save/import with the existing confirmation gates; health advertises
`indicator_operand_offset_runtimes`. New squeeze templates expose `bands.period`, `bands.stdev_multiplier`,
`channel.period`, `channel.atr_period`, and `channel.multiplier` axes using these shared reads.
Do not invent unlisted kinds or pass through a TA library.
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
clocks, each additional instrument, and each reference instrument — an omitted fingerprint binds the newest complete dataset
the catalog lists for that product and timeframe from the configured ingestion provider (`coinbase`
with credentials, otherwise `demo`; the same rows as `thytrader-operator data-catalog`). Explicit
fingerprints are used exactly as sent, and you may mix the two (for example send only
`dataset_fingerprint` and let the HTF clock bind). Every response echoes `bound_datasets`:
`[{product_id, timeframe, role: decision|filter|indicator|reference, reference_id?,
dataset_fingerprint, source: request|latest_catalog}]` (`reference_id` only on `reference` rows), and the bound fingerprints are part of the run's identity, so record them
with the result. A clock with no cataloged dataset fails closed with HTTP 422 `datasets_missing`:
`detail.missing` lists each `{product_id, timeframe, role}` and the message names the exact
`uv run thytrader-data watch-add … --confirm` and `ingest … --confirm` commands; nothing runs.

**Reference datasets** ([ADR 0096](../../docs/decisions/0096-reference-instruments.md)) bind the
same way, once per strategy: an omitted entry binds the newest complete catalog dataset for the
reference's `product_id` + `timeframe` (`role: reference`, `reference_id` set). To pin one, send
`reference_dataset_fingerprints: [{reference_id, product_id, timeframe, dataset_fingerprint}]`
(each must equal a declared reference; `--reference-dataset-fingerprint REFERENCE_ID=sha256:…` on
the local publish CLI). Omitted bounds shrink to the reference's closed-bar coverage (including its
derived warmup); explicit bounds beyond it are refused with `backtest_window_rejected`. Studies carry
the bindings into every window and candidate. A cross-market study re-targets only the traded
instrument: the reference stays fixed (BTC stays BTC) and every market leg reads the same reference
series; a market in a different quote currency than the reference is refused (`quote currency`).

**Study bounds may be omitted.** `plan-study` / `submit-study` may omit both `evaluation_start` and
`evaluation_end` (never just one). The server then uses the common covered window: the intersection
of every child backtest's default window (each market of a cross-market study, and every sweep/WFO
candidate, whose derived warmup may be longer). Plan, submit, and async 202 responses echo
`evaluation_start` / `evaluation_end` and `bound_datasets`; the request fingerprint covers the filled
bounds. An async submit validates and pins those inputs before queueing; the worker plans it.
An oversized or infeasible async study fails with `failed_phase: plan` and a structured
`error_code` / `failed_detail` on `show-research-job`. Use `plan-study` for an immediate read-only
preflight. Synchronous submission retains its small-budget planning preflight and immediate 422s.

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

`explain-bars` (`GET /api/v1/backtests/{result_fingerprint}/bar-explanations`, ADR 0116) is the
bounded page that joins that verified trace to the immutable result's own fills and equity marks.
It is read-only, needs no `--confirm`, and is HTTP-only (`--local` is rejected). `outside_trace`
lists fills whose candle was not a signal bar, including evaluation-end liquidation. A trace that
does not match the result is `bar_explanations_unavailable`, not a guessed explanation.

## Maker/taker rates

`GET /api/v1/fees` (or `thytrader-operator fees`) includes `suggested_maker_fee_rate` /
`suggested_taker_fee_rate` when Coinbase credentials are present. They are the **account's own
reported Coinbase rates** (`suggestion_source=coinbase_account`; for example 0.005 / 0.009 on an
Intro tier) — what live fills are billed at
([ADR 0090](../../docs/decisions/0090-research-correctness-optional-take-profit-diagnostics.md)).
`schedule_maker_fee_rate` / `schedule_taker_fee_rate` with `suggestion_schedule_tier_id` and
`suggestion_schedule_version` are the pinned public band for the same volume, **context only**:
never copy them as defaults. Copy `suggested_*` into `submit-backtest` / `submit-study` JSON unless
the operator supplied custom rates. Paper starts that omit rates take the same account rates
automatically ([ADR 0122](../../docs/decisions/0122-paper-fees-default-to-account-rates.md)).
Demo or missing credentials set `suggestion_source=unavailable` — do **not** use dashboard demo
`maker_fee_rate` / `taker_fee_rate` as research defaults, and do not invent a tier. The request must
still include explicit rates; submitted runs fingerprint those values. They are modeled
`CostAssumptions`, not observed Coinbase fills. Resting entries and take-profits use the **maker**
rate; stop, time, and end-of-window exits use the **taker** rate. Paper deploy accepts optional `maker_fee_rate` / `taker_fee_rate`
through `thytrader-runtime` ([ADR 0048](../../docs/decisions/0048-paper-deploy-fee-fields.md));
omitted rates store the account's own rates, or the start is refused when they cannot be read
([ADR 0122](../../docs/decisions/0122-paper-fees-default-to-account-rates.md)). Paper fills
remain modeled, not observed Coinbase fills. Live Coinbase fees stay venue-recorded.

## Where research runs (research worker queue)

Every backtest, study, and portfolio backtest runs in the `research-worker` service, never in the
API ([ADR 0092](../../docs/decisions/0092-research-worker-pool.md)). The API validates, pins inputs,
and queues async studies; the worker plans and executes them (ADR 0103). Synchronous studies keep
their small-budget planning preflight. The pool runs at most `THYTRADER_RESEARCH_WORKER_COUNT` jobs at once (default 2), oldest
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
  example `per-market`) so you can list and clean them up later. `--origin research` (HTTP
  `?origin=research`) keeps strategies tagged `claude-research` or any `research-*` tag;
  `--origin operator` keeps every other strategy (the person's own, the UI's "Mine" view); the
  default `all` applies no origin filter. It combines with `--tag`; pass the same `--origin` with
  `--cursor` ([ADR 0098](../../docs/decisions/0098-library-views-book-marks-portfolio-fills.md)).
  Tag strategies your research run creates `claude-research` so they stay out of the person's
  default "Mine" view. Cross-market and sweep variants
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
  bindings, except retained execution evidence below (ADR 0111).
  It also removes the strategy's portfolio sleeves; each removal is journaled in its portfolio and
  counted as `portfolio_sleeves`.
  It is refused with HTTP 409 `strategy_has_active_deployments` (with `deployment_ids`) while any
  bot of the strategy is running or paused — stop it with `skills/thytrader-runtime/SKILL.md`
  first (that is a runtime-lane action; this skill never stops bots). Stopped PAPER and LIVE
  deployments are **kept** with orders, fills, positions, trade reasons, breaker latches, and the
  snapshot they ran; they are detached (`strategy_id: null`, `strategy_deleted: true`,
  `strategy_name` kept). Financial loss evidence is never deleted by this command, and deletion
  does not reset daily loss or a latch. `counts.paper_deployments` counts removals (now zero),
  while `live_deployments_kept` keeps its existing meaning. If the active risk policy allocated capital to the strategy, the
  same transaction publishes the next risk-policy version without that allocation
  (`risk_policy_republished: true`). The output `counts` lists what was removed.
- `bulk-delete-strategies --strategy-id … --dry-run` previews each id (`would_delete` with
  `counts`, `blocked`, or `not_found`) without writing; run it first and show the user the list.
  `--confirm` executes and returns one result per id (`deleted`, `blocked`, `not_found`, `failed`);
  partial success is normal — report each id's outcome. At most 100 ids per call.
- `bulk-delete-strategies --tag TAG --dry-run` previews every strategy tagged `TAG` (the CLI pages
  the tagged library, then sends batches of 100 to the same bulk route); `--tag TAG --confirm`
  deletes them. The output adds `tag` and `matched` and sums the per-batch counts. Safety is the
  same as by id: running or paused bots block their strategy (`blocked`) and stopped paper/live
  books and their risk evidence are kept. `--tag` and `--strategy-id` cannot be combined.
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

Library pages add `origin_counts` (`operator`, `research`, `all`), counting all rows matching the
current tag before pagination or origin filtering. These power the UI's Mine / Research / All badges.

## Frozen campaigns, prospective validation, and economic preflight (ADR 0109)

Use the research HTTP lane for these commands; `--local` is not supported. Creation
freezes rules and authorizes research children only. It never deploys or cancels orders.

| Task | Command |
| --- | --- |
| Freeze a campaign | `uv run thytrader-research create-campaign --file campaign.json --confirm` |
| List campaigns | `uv run thytrader-research list-campaigns --limit 20` |
| Inspect frozen intent and child evidence | `uv run thytrader-research show-campaign --campaign-id UUID` |
| Advance approved research now | `uv run thytrader-research refresh-campaign --campaign-id UUID --confirm` |
| Export small result projections | `uv run thytrader-research export-results --limit 100 [--cursor CURSOR]` |
| Calculate order economics | `uv run thytrader-research economics --file economics.json` |

Campaign JSON has `name`, `kind: historical|prospective`, UTC `deadline`, `gates`, and
`cases: [{key, request: BacktestStartRequest}]`. Every case requires explicit
`evaluation_start` and `evaluation_end`; supply a `strategy_id` and the usual capital
and cost fields. Omitted datasets bind only once, when complete verified data can
cover the frozen window and warmup. Optionally set a case's `strategy_fingerprint`
to an already frozen snapshot of that same strategy; otherwise creation snapshots
its current valid rules. Later strategy edits never retune a campaign.

Gates are `minimum_trades` (default 10), `minimum_net_return_fraction` (default 0;
return must strictly exceed it), and `maximum_drawdown_fraction` (default 0.15).
A prospective case must begin at or after freezing. The existing research worker
advances approved campaigns automatically: `waiting_for_data` → `queued` → `running`
→ `passed`, `failed_gate`, `insufficient_sample`, `failed`, or `expired`. Passing is
research evidence, not permission to trade. Missing future candles never count as
validation; expired or undersampled cases never pass. Concurrent refreshes and
restarts queue one child identity per case. A failed child is retained; review it
before creating a replacement campaign.

Read/download one frozen report at `GET /api/v1/research/campaigns/{id}` and
`GET /api/v1/research/campaigns/{id}/export` (CSV). The UI is `/research`, linked from
Backtests, and offers campaign creation, status, CSV downloads, and economic preflight.

Economic preflight JSON uses `side`, `entry_price`, `quantity`, `stop_price`, optional
`target_price`, `maker_fee_rate`, `taker_fee_rate`, optional `fixed_slippage_bps` and
`spread_bps`. `POST /api/v1/research/economics` is read-only. It reports both entry and
exit fees, maker break-even, net TP return/PnL and stressed taker stop PnL. Supplied
fees are modeled assumptions, not a quote or permission to submit an order.
This read-only POST still requires installation authentication (and browser CSRF).
The CLI sends the credential automatically; calculation does not require `--confirm`.

An optional strategy field `entry.economic_guard: {minimum_net_target_return_fraction:
"0.002"}` uses the same net maker-target calculation in backtest, paper and live.
Absent means existing behavior and fingerprints remain unchanged. An enabled guard
requires a target and sufficient net return after both maker fees; it records
`NET_TARGET_BELOW_MINIMUM` without creating an order when refused. Live also requires
a current fee profile (`ECONOMICS_FEE_UNAVAILABLE` otherwise). Paper uses its declared
fee schedule. The guard does not bypass risk, venue sizing, or stop checks.
Replacement entries recheck the guard. Live repricing carries the observed fee tier;
a simulated reprice below the net hurdle expires the pending entry.

Backtests and studies accept optional `execution_stress` with `entry_latency_bars`
(additional completed bars before entry activation, 0–100), `maker_penetration_bps`
(0–1000, entries and targets), and `entry_fill_fraction` (>0 to 1). A partial entry
fills once at its posted limit and cancels the remainder. Latency does not consume
active entry wait; a reprice does not repeat activation latency. The profile is
fingerprinted in run costs and echoed in bounded results. These are deterministic
candle stresses, not observed order-book queue/latency or partial-fill reconstruction.
Omitting the profile preserves the original simulation semantics and fingerprints.

Default result summary reads and `export-results` verify publication/source digests
inside PostgreSQL without loading trade/equity arrays or historical Parquet.
`verification_scope: publication` explicitly distinguishes this from `detail=full`
artifact verification. Old publications may lack derived `metrics`; their warnings
say so. Fetch `/metrics` or the full result when that detail is needed. General bulk
export uses the existing offset cursor, so new concurrent publications can shift
pages; campaign reports pin child identities and are stable for that manifest.

## Closed-trade fee attribution

`cost_attribution` is a `thytrader-cost-attribution-v1` report outside canonical result
bytes. It contains `result_fingerprint`, `run_fingerprint`, its own
`attribution_fingerprint`, and `trade_count`. All amounts are exact decimal strings
in the strategy's quote currency:

- `fill_price_pnl_before_fees`: sum of recorded trade `gross_pnl`; modeled fill prices
  already include spread and slippage. Do not subtract either cost again.
- `entry_fees` and `exit_fees`: sums of recorded entry and exit fill fees.
- `net_pnl`: sum of recorded closed-trade net PnL.
- `accounting_residual`: net minus (before-fees PnL minus both fee totals).
- `summary_net_pnl_delta`: canonical summary net PnL minus closed-trade net PnL.

The simulator's Decimal64 arithmetic can leave tiny rounding differences; the last
two fields disclose them rather than attributing them to fees. Summary `gross_profit`
and `gross_loss` group winning/losing **net** trade PnL and are not before-fee totals.

`uv run thytrader-research show-result --result-fingerprint sha256:…` and
`export-results --limit 100 [--cursor CURSOR]` include this field. New publications
record it in Alembic 0064's nullable metadata column. Bounded reads do not load trade
or equity arrays: legacy missing metadata is `null` with a warning, never zero.
`GET /api/v1/backtests/{fp}?detail=full`, operator `performance --result-fingerprint`,
and explicit `show-result --local` compute it from fully read evidence. A verified
republish fills missing metadata without replacing recorded attribution or changing
result fingerprints. The result UI shows all four totals and reconciliation details.
Stored reports with missing or placeholder attribution digests fail integrity validation.
No new CLI flags or trading authority are introduced.

Use `show-result` or `export-results` for recorded attribution decimals instead of reading rounded
monetary cards from a screenshot.
