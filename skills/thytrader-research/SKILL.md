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
- **Stops and targets on bar extremes.** On the fill candle only the stop can trigger (stop-first).
  From the next candle the take-profit rests. The stop is always checked first: a candle that
  touches both the stop and the take-profit is resolved as the stop (conservative; paper does the
  same). Otherwise a touched take-profit fills at the target (maker). Stops fill as takers at the stop or the worse
  gapped open; ATR trailing ratchets after the check. Time exits sell at the close.
- **End of window.** Open inventory sells at the open of the `evaluation_end` candle; nothing else
  happens on that candle.
- **Costs.** Maker fee on resting entries/take-profits; taker fee plus `fixed_slippage_bps` on stop,
  time, and end exits. Optional `spread_bps` (default 0, max 1000) is a constant total spread
  **stress**: taker exits cross half of it, stops trigger and positions mark on the stressed
  bid/ask, maker fills stay at the limit. Compare the same strategy at 0 / 10 / 25 / 50 bps.
- **Honesty.** Candles do not show queue position: a touched limit is assumed to fill fully.
  Every summary lists `validity_limits` (`maker_touch_full_fill`, `stop_before_tp_same_bar`, and
  `spot_short_synthetic` for shorts); read them before any deployment claim. Backtests are
  simulated research evidence, never paper or live fills.

## Commands

| Need | Command |
|---|---|
| List the strategy library | `uv run thytrader-research list-strategies [--limit 50] [--cursor CURSOR]` |
| Show one strategy (document, validation, revision, current fingerprint) | `uv run thytrader-research show-strategy --strategy-id UUID` |
| Show one snapshot a result or bot used | `uv run thytrader-research show-snapshot --strategy-fingerprint sha256:…` |
| Create a strategy from a template | `uv run thytrader-research create-strategy [--template rsi-mean-reversion] [--product-id ETH-USD] [--timeframe 5m] [--experiential-model-id UUID] --confirm` |
| Save (edit) a strategy in place | `uv run thytrader-research save-strategy --strategy-id UUID --file document.json --revision N --confirm` |
| Import a JSON document as a new strategy | `uv run thytrader-research import-strategy --file document.json --confirm` |
| Clone a strategy into a new identity | `uv run thytrader-research clone-strategy --strategy-id UUID --confirm` |
| Delete one strategy (hard delete) | `uv run thytrader-research delete-strategy --strategy-id UUID --confirm` |
| Preview a bulk delete | `uv run thytrader-research bulk-delete-strategies --strategy-id UUID [--strategy-id UUID …] --dry-run` |
| Bulk delete strategies | `uv run thytrader-research bulk-delete-strategies --strategy-id UUID [--strategy-id UUID …] --confirm` |
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
| Read back a study after an ambiguous submit | `uv run thytrader-research find-study-by-request --request-fingerprint sha256:…` |
| Cancel one queued or running research job | `uv run thytrader-research cancel-research-job --job-id UUID --confirm` |
| List persisted study catalog rows | `uv run thytrader-research list-studies [--kind parameter_sweep] [--strategy-id UUID] [--limit 50]` |
| Show one persisted study summary | `uv run thytrader-research show-study --study-fingerprint sha256:…` |
| List result summaries | `uv run thytrader-research list-results [--strategy-id UUID \| --strategy-fingerprint sha256:…] [--limit 20] [--cursor CURSOR]` |
| Show one result summary | `uv run thytrader-research show-result --result-fingerprint sha256:…` |
| Show IS/OOS/sweep/paper/live evidence | `uv run thytrader-research show-evidence --strategy-fingerprint sha256:…` |

`list-results`, `show-result`, `show-strategy`, `show-snapshot`, `show-evidence`, `list-templates`, `show-template`, `backtest-model`, `plan-study`,
`list-studies`, `list-strategies`, and
`show-study` are read-only and
do not use `--confirm`. `list-results` and `list-strategies` page at most 100 rows (`has_more` /
`next_cursor`). Default `show-study` includes `window_pnl` headlines (label, role, PnL, trades)
without child equity curves; `?detail=full` still returns `windows[]`. Queued research jobs report
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
(`window_count`, aggregates, stitch metadata without `points`). Pass `?detail=full` for child
`windows`. `show-study` uses the default summary. Operator
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
`supertrend-trend`, `squeeze-breakout`, `zscore-mean-reversion`), `--product-id`, and
`--timeframe` (any ingested venue clock) for another USD, USDC, or USDT spot product. Paper and live start by `strategy_id` through `thytrader-runtime`; the server snapshots the current definition.
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
LTF/HTF/extra-TF fingerprints. `dataset_fingerprint` remains the primary instrument. All of these
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
A synchronous submit waits 30 s. If it prints `Timed out after 30 s waiting for the ThyTrader API to
answer POST /api/v1/backtests`, the backtest may still be running: check `list-results` before
submitting again, or re-run with `--async` ([ADR 0085](../../docs/decisions/0085-fast-research-ingest.md)).
Required assumptions: `initial_quote_balance`, `maker_fee_rate`, `taker_fee_rate`,
`fixed_slippage_bps`; optional `spread_bps` stress. Never send `engine_contract_version`.

`show-result` returns the result summary, derived `metrics`, and the published `costs`
(including `spread_bps`). It copies the snapshot's decision clock (`1m` through `1d`, including `2h` and
`4h`) into the compact summary `timeframe`. It does not default every result to `1h`.

## Maker/taker rates

`GET /api/v1/fees` includes `suggested_maker_fee_rate` / `suggested_taker_fee_rate` when Coinbase
credentials are present (`suggestion_source=coinbase_fee_schedule`, plus tier id, schedule version,
and `fetched_at`). Copy those into `submit-backtest` JSON unless the operator supplied custom rates.
Demo or missing credentials set `suggestion_source=unavailable` — do **not** use dashboard demo
`maker_fee_rate` / `taker_fee_rate` as research defaults, and do not invent a tier. The request must
still include explicit rates; submitted runs fingerprint those values. They are modeled
`CostAssumptions`, not observed Coinbase fills. Resting entries and take-profits use the **maker**
rate; stop, time, and end-of-window exits use the **taker** rate. Paper deploy accepts optional `maker_fee_rate` / `taker_fee_rate`
through `thytrader-runtime` ([ADR 0048](../../docs/decisions/0048-paper-deploy-fee-fields.md));
omitted paper rates keep the documented `0.001` maker / `0.002` taker schedule. Those paper rates
are also modeled assumptions, not observed Coinbase fills. Live Coinbase fees stay venue-recorded.

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
  `current_fingerprint`, newest `backtest`, `paper_live` status, and `active_deployment_count`.
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
