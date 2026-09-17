---
name: thytrader-research
description: >-
  Create ThyTrader strategy drafts, publish immutable versions, and submit or
  compare deterministic backtests and composed research studies through the
  confirmation-gated thytrader-research CLI. Use when the user asks to create a
  strategy, publish, run a backtest, or run an OOS / walk-forward / cross-market /
  parameter-sweep / WFO study, or to list persisted study catalog rows. Requires explicit --confirm for every mutation. Never deploys,
  paper-trades, live-trades, arms, or cancels orders.
---

# ThyTrader research

Bounded research mutations only. This skill is not an extension of `thytrader-operator` and has no paper, live, arming, cancellation, or kill-switch authority.

Default transport is the loopback HTTP API (`THYTRADER_API_BASE_URL` or `http://127.0.0.1:8200`). Pass `--local` only when you intentionally want PostgreSQL stores. Do not fall back from HTTP to the database if the API is down.

Existing HTTP contracts (`POST /api/v1/strategies`, `POST /api/v1/strategies/{id}/publish`,
`POST /api/v1/backtests`, `POST /api/v1/research/studies`, `GET /api/v1/research/studies`) remain valid. The agent-facing mutation
path is `uv run thytrader-research` with `--confirm`.

In-app operator chat (`/chat`, `/api/v1/operator-chat`) may invoke these same HTTP routes. It is
not extra authority: mutations still need in-app confirmation. Do not treat chat as this skill.

## Hard stop

When operating a running instance, do not edit `src/`, `compose.yaml`, Dockerfiles, Alembic, or tests.
Do not search the tree for a code patch. Report failures through this skill. Every HTTP command
preflights the full `/health/ready` ops contract. Rebuild or restart only with `make run` when the
user asked, or when the CLI reports a version or ops-contract mismatch, or HTTP 404 on an agent
route while `/health/ready` is 200 (the shared stale-image signal). Matching `0.1.0` alone is not
current-image evidence. Open the `ops/` workspace instead of
the git root. Run every `uv run thytrader-*` command from the repository root (the parent of `ops/`).

## When to pick backtest engine V1 vs V2 vs V3 vs V4

These are **parallel research contracts**, not product releases. Later engines do not obsolete
earlier fingerprints. Each run fingerprints its engine; results stay comparable only within the same
contract. Full semantics: `docs/architecture/backtest-simulation.md` and
[ADR 0062](../../docs/decisions/0062-research-paper-semantics-audit-stage-4.md). Do not confuse
engine versions with Coinbase Advanced Trade REST **v3** (live order API).

| Engine | Use when | Not for |
|---|---|---|
| `thytrader-bar-backtest-v1` | Fast baseline: signal on close → fill at **next open** as marketable/taker-style (fixed slippage + taker fee). Good first pass and cheap compare. | Matching paper/live maker-limit behavior; spread-stress sweeps |
| `thytrader-bar-backtest-v2` | Same event order as V1, plus explicit constant **`spread_bps` stress** (not observed Coinbase bid/ask). Compare the same strategy at 0 / 10 / 25 / 50 bps. `spread_bps=0` matches V1 economics. | Claiming live fill quality; maker-rest realism |
| `thytrader-bar-backtest-v3` | Historical maker-limit contract (pre-0062): post-only limit at signal close, terminal candle may process intrabar exits beyond the declared boundary. Keep for reproducing published v3 evidence only. | Walk-forward/OOS selection, new paper/live comparison, or claiming corrected terminal boundaries |
| `thytrader-bar-backtest-v4` | **Default for new maker research** ([ADR 0062](../../docs/decisions/0062-research-paper-semantics-audit-stage-4.md)): v3 maker semantics plus causal evaluation terminal, taker slippage on stops/time/end, causal same-bar trailing, and `validity_limits` on summaries. | Spread-stress sweeps (no `spread_bps`); silently comparing to pre-0062 v3 bytes |

Default guidance: v4 for OOS/walk-forward/sweeps and paper/live comparison; v2 for friction
stress; v1 for quick smoke tests; v3 only when reproducing an existing v3 fingerprint. Never treat
a backtest as a paper or live fill. Read `validity_limits` on v4 summaries before deployment
claims — they document maker touch-fill, TP-before-stop ordering, and spot-short modeling limits.

## Commands

| Need | Command |
|---|---|
| Create a template draft | `uv run thytrader-research create-draft [--template rsi-mean-reversion] [--product-id ETH-USD] [--timeframe 5m] [--experiential-model-id UUID] --confirm` |
| List draft templates | `uv run thytrader-research list-templates` |
| Show the V1/V2/V3/V4 engine-support matrix | `uv run thytrader-research engine-support` |
| Save a draft from JSON | `uv run thytrader-research save-draft --file definition.json --revision N --confirm` |
| Publish the matching draft | `uv run thytrader-research publish --strategy-id UUID --confirm` |
| Submit an idempotent backtest | `uv run thytrader-research submit-backtest --file request.json --confirm` |
| Plan OOS / walk-forward / cross-market / sweep / WFO windows | `uv run thytrader-research plan-study --file study.json` |
| Submit a composed research study | `uv run thytrader-research submit-study --file study.json --confirm` |
| List persisted study catalog rows | `uv run thytrader-research list-studies [--kind parameter_sweep] [--limit 50]` |
| Show one persisted study summary | `uv run thytrader-research show-study --study-fingerprint sha256:…` |
| List result summaries | `uv run thytrader-research list-results [--strategy-fingerprint sha256:…]` |
| Show one result summary | `uv run thytrader-research show-result --result-fingerprint sha256:…` |

`list-results`, `show-result`, `list-templates`, `engine-support`, `plan-study`, `list-studies`, and
`show-study` are read-only and
do not use `--confirm`. `submit-study` requires `--confirm`. Studies compose existing V1/V2/V3/V4
backtests. In-sample-only studies expose `oos_window_count=0` and absent OOS means; do not treat
`mean_is_return_fraction` as out-of-sample evidence. `walk_forward` validation freezes one published fingerprint. `parameter_sweep` and
`walk_forward_optimization` select among published fingerprints or `parameter_axes`. Axes default
to indicator `period` / `fast_period` / `slow_period` / `signal_period` / `k_period` / `d_period` /
`stdev_multiplier` / `value`. Optional `target` may be `sizing` (`risk_fraction`, `min_quote_notional`,
`max_quote_notional`), `exits` (`initial_stop_multiple`, `take_profit_multiple`,
`trailing_stop_multiple`, `max_bars_held`), `execution` (`max_entry_wait_bars`), or
`entry_literal` / `htf_literal` (`literal`, optional `condition_operator`). Cartesian product ≤ 8.
Product and timeframe are not sweepable. Selection uses only in-sample `selection_metric`; it does
not look ahead from OOS.
`plan-study` derives axis candidates in memory. `submit-study --confirm` publishes missing derived
documents, then submits ordinary backtests, then persists a catalog row. `list-studies` is
newest-first summaries. `show-study` omits child windows and stitched equity points. Operator
`thytrader-operator studies` is the same catalog. Durable storage is PostgreSQL; `--local` without
a database is unavailable, not empty. Stitched OOS equity compounds non-overlapping window
returns for `walk_forward` OOS and selected WFO OOS; overlapping OOS and embargo gaps are not
interpolated. Parameter-sweep aggregates are not an out-of-sample claim. Cross-market studies
need 2–8 published single-instrument strategies on distinct products. See
[`docs/architecture/research-studies.md`](../../docs/architecture/research-studies.md).

`create-draft` defaults to template `ema-trend`, `BTC-USD` / `1h`. Pass `--template`
(`ema-trend`, `rsi-mean-reversion`, `macd-trend`, `bollinger-mean-reversion`), `--product-id`, and
`--timeframe` (any ingested venue clock) for another USD spot product. Paper and live may start that published fingerprint.
Optional `--experiential-model-id` (HTTP only; `--local` refuses) loads
`GET /api/v1/memory/models/{id}` fail-closed and merges `experiential_advisory` into the
create-draft JSON. It does not change published strategy semantics, place orders, or arm live
trading. Train models with `skills/thytrader-memory/SKILL.md`.
Optional `htf_filter` (ADR 0025) is a higher-timeframe closed-bar filter AND-ed with LTF entry.
`create-draft` does not add it. `save-draft` JSON may include the block. `submit-backtest` JSON must
include `htf_dataset_fingerprint` (distinct from `dataset_fingerprint`) when the published strategy
declares `htf_filter`, and must omit it otherwise. Extra indicator clocks that are not already
`htf_filter.timeframe` require `indicator_dataset_fingerprints` (`[{timeframe, dataset_fingerprint}, …]`
ordered by increasing duration, each distinct from LTF and HTF). Ingest those extra clocks with
`skills/thytrader-data/SKILL.md` before naming fingerprints. Multi-instrument published documents
require `additional_instrument_datasets` on submit-backtest JSON: one `{product_id, dataset_fingerprint,
htf_dataset_fingerprint?, indicator_dataset_fingerprints?}` per extra covered product, ordered by
`product_id`, omitted when the document has no extra products. Each extra product needs a complete
Coinbase dataset on the decision clock; HTF and extra-TF fingerprints are required iff the document
declares those clocks. Identities must be unique and distinct from the primary LTF/HTF/extra-TF
fingerprints. `dataset_fingerprint` remains the primary instrument. Research engines V1/V2/V3 evaluate
last-completed extra-TF and HTF bars only. Paper and live evaluate the same last-completed bars on
live complete-only candles; they do not bind frozen extra-TF or HTF fingerprints.

Discover implemented indicator kinds with `uv run thytrader-operator indicators` before authoring.
Shipped kinds: `ema`, `sma`, `rsi`, `atr`, `volume_sma`, `highest`, `lowest`, `stdev` (population),
`stdev_sample` (sample / `N-1`), `roc`, `williams_r` (high/low/close), `cci` (high/low/close), `wma`,
`momentum`, `mfi` (high/low/close/volume), `macd` (close; `fast_period`/`slow_period`/`signal_period`,
fast < slow; series `macd`/`signal`/`histogram`), `bollinger` (close; `period` plus
`stdev_multiplier`; series `middle`/`upper`/`lower`), `stochastic` (high/low/close; `k_period` 2–100
and `d_period` 2–500; series `k`/`d`), `adx` (high/low/close; `period` 2–100; series
`adx`/`plus_di`/`minus_di`; warmup `2 * period - 1`), `identity` (one of open/high/low/close/volume,
empty parameters), `constant` (`parameters.value`, no input). Rolling `ema`/`sma`/`wma`/`highest`/
`lowest`/`stdev`/`stdev_sample`/`roc`/`momentum` accept one of open/high/low/close/volume. RSI,
volume SMA, MACD, and Bollinger stay locked. Single-output operands omit `series`. Multi-series
operands must name one declared series. Do not invent unlisted kinds or pass through a TA library.
Optional per-indicator `timeframe` on LTF-list indicators must be a coarser integer-multiple venue
clock; omit it to keep the decision clock. `constant` and HTF-filter indicators omit `timeframe`.
`crosses_above` / `crosses_below` need two indicator operands. Compare an indicator to a
level with `greater_than*` / `less_than*` and a `literal`, or declare a `constant` kind and cross that
id. Copy a candle field with `identity`. `save-draft` prints the first Pydantic
validation message; do not treat a generic “failed safely” string as success. HTTP 422 that lists
backtest engines through v1/v2 only, or through v3 without v4, is a stale Compose image — rebuild
with `make run`. A matching `/health/ready` ops contract must advertise v4 before v4
`submit-backtest` requests ([ADR 0066](../../docs/decisions/0066-research-ops-contract-v4.md)).

`submit-backtest` may omit both `evaluation_start` and `evaluation_end`. The server fills the
dataset's usable window (warmup before the start, one bar after the end for next-open fill). If
supplied dates do not fit, the API returns 422 with a suggested ISO range. Do not invent a window
that the catalog cannot cover. Name an explicit engine contract in the request (`thytrader-bar-backtest-v1`,
`…-v2`, `…-v3`, or `…-v4`) per the table above. Prefer v4 for new maker research unless
reproducing a published v3 fingerprint.

## Maker/taker rates

`GET /api/v1/fees` includes `suggested_maker_fee_rate` / `suggested_taker_fee_rate` when Coinbase
credentials are present (`suggestion_source=coinbase_fee_schedule`, plus tier id, schedule version,
and `fetched_at`). Copy those into `submit-backtest` JSON unless the operator supplied custom rates.
Demo or missing credentials set `suggestion_source=unavailable` — do **not** use dashboard demo
`maker_fee_rate` / `taker_fee_rate` as research defaults, and do not invent a tier. The request must
still include explicit rates; submitted runs fingerprint those values. They are modeled
`CostAssumptions`, not observed Coinbase fills. V1/V2 next-open fills use the **taker** rate even
when the strategy prefers maker. Paper deploy accepts optional `maker_fee_rate` / `taker_fee_rate`
through `thytrader-runtime` ([ADR 0048](../../docs/decisions/0048-paper-deploy-fee-fields.md));
omitted paper rates keep the documented `0.001` maker / `0.002` taker schedule. Those paper rates
are also modeled assumptions, not observed Coinbase fills. Live Coinbase fees stay venue-recorded.

## Confirmation

- Never run `create-draft`, `save-draft`, `publish`, `submit-backtest`, or `submit-study` unless the user explicitly asked for that mutation **and** `--confirm` is present, unless the user explicitly asked to operate under YOLO **and** operator `configuration` / `thytrader-playbook status` shows the `research` tier enabled.
- `--local` research always requires `--confirm` (YOLO is HTTP-only).
- If `--confirm` is missing in Safe mode, the CLI exits without writing. Do not retry with `--confirm` unless the user asked you to.
- Successful mutations print JSON identities (`strategy_id`, `strategy_fingerprint`, `run_fingerprint`, `result_fingerprint`, `study_fingerprint`). Keep those identities.

## Forbidden

- Deployments, pause/resume/stop, Coinbase orders, risk-limit edits, kill switches
- Direct PostgreSQL access as the public agent contract
- Treating a backtest as a live or paper fill
- Archiving as part of this skill (out of scope)
- Editing application source to change strategy or backtest semantics on a running instance

Diagnose a running instance with `skills/thytrader-operator/SKILL.md` first when health is unknown. Coverage and ingest are `skills/thytrader-data/SKILL.md`. Paper/live control is `skills/thytrader-runtime/SKILL.md`. Strategy `timeframe` may be any ingested venue clock for backtests, paper, and live. Published `htf_filter` is executable in paper and live.
