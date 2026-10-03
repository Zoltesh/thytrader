# Operator report schema

Every JSON report includes:

- `schema_version`: `thytrader-operator-report-v1`
- `report_kind`: `health` \| `configuration` \| `exchange` \| `market_data` \| `data_catalog` \| `products` \| `indicators` \| `strategies` \| `performance` \| `risk` \| `reconciliation` \| `runtime` \| `monitor` \| `studies` \| `trade_reasons` \| `decisions` \| `support_bundle` \| `portfolio` \| `fees` \| `portfolios`
- `application_version`: ThyTrader package version
- `generated_at`: timezone-aware UTC timestamp
- `timezone`: `UTC`
- `overall_status`: `healthy` \| `degraded` \| `failed`
- `components[]`: `name`, `status`, `reason_code`, `detail`
- `redaction`: `secrets_redacted`, `raw_environment_omitted`, `account_identifiers_omitted`, `balances_omitted`. Most reports set all four true. The `portfolio` report sets `balances_omitted=false` so operators can read cash amounts without credentials. Account holdings also remain on `GET /api/v1/portfolio`. Deployment quantities live on `thytrader-runtime show` / `GET /api/v1/deployments/{id}`.
- `partial_result_warnings[]`
- `recommended_next_action`
- `payload`: report-specific object

`reason_code` matches `^[A-Z][A-Z0-9_]{0,63}$`.

Performance `payload.currency` is the exact snapshot instrument quote (`USD`, `USDC`, or `USDT`) for strategy-backed reports, or the parsed product quote for discretionary deployments. It is `null` with a `partial_result_warnings[]` explanation when provenance cannot be established; never read a null as USDC or convert a USD amount by relabeling it. Performance `payload.mode` is `backtest`, `paper`, or `live`. Backtest metrics come from an immutable result. Backtest `payload.metrics` is the derived `thytrader-performance-metrics-v1` block (Sharpe, Sortino, Calmar, SQN, CAGR, annualized volatility, max consecutive losses, exposure fraction, mark-to-mark buy-and-hold) and does not change result fingerprints. Backtest `payload.window` (ADR 0094) states the evaluated bars: `timeframe`, `evaluation_start`, `evaluation_end` (exclusive; its bar's open liquidates what is held), `first_evaluated_bar`, `last_evaluated_bar`, `evaluation_bars`, `warmup_bars`, and `warmup_start`, derived from the result's run outside the fingerprinted result bytes (`null` for paper/live and when the run cannot be read). Two backtests are comparable only when their `evaluation_start` / `evaluation_end` match: omitted bounds start after each strategy's own warmup. Backtest performance carries no engine field: every result uses the single `thytrader-backtest` model; `total_spread_cost` is present only when the run used `spread_bps` stress. Paper/live metrics come from a fill ledger: `trade_count` is round trips, `total_net_pnl` / return / drawdown use recorded fills plus last-close marks for every open product book. `books[]` reports per-product `trade_count`, `total_net_pnl`, and `mark_complete`; deployment-level `marked_exposure` and `mark_complete` aggregate across books. Open inventory without a mark leaves `total_net_pnl` null (`MISSING_MARK`) instead of inventing equity. Drawdown is fill-event marks, not a bar equity curve.

The `runtime` payload lists deployment identities plus risk and reconciliation findings. It also
reports `user_order_feed` lifecycle state (`connected` / `stale` / `disabled`, timestamps) without
JWT material or order payloads. It omits cash, quantities, and order payloads. Each deployment
includes `kind` (`strategy` or `discretionary`) and optional strategy identity. Multi-instrument
deployments add `books[]`: `product_id`, `phase`, `side` (`null` when flat),
`protection_status` (`flat` \| `covered` \| `unprotected` \| `unknown`), `position_state`
(`flat` \| `entering` \| `open_protected` \| `open_unprotected` \| `open_unverified` \|
`exiting`), and `exit_in_flight` (boolean). Each deployment row carries the worst book's
`position_state` and `exit_in_flight` (`null` only when its books could not be read). `phase`
stays the raw worker state; `pending_exit` includes resting TP/SL protection, which is
`open_protected` (ADR 0097). No quantities. Do not
treat the deployment-level `product_id` as the only open book
([ADR 0060](../../../docs/decisions/0060-multi-book-deployment-api.md)). The `strategies` payload
is `{strategies: [...], deployments: [...]}` ([ADR 0082](../../../docs/decisions/0082-strategy-root-mutable-strategies-auto-snapshots.md)).
Each `strategies[]` row is `strategy_id`, `name`, `revision`, `valid`, `current_fingerprint`
(`null` when the saved definition is invalid), `product_id` / `timeframe` (`null` when the saved
document lacks a usable value), and `updated_at`. There are no `drafts` or `publications` keys and
no version numbers. Deployment rows (here and in `runtime`) carry `strategy_id` (`null` for a
stopped live book whose strategy was deleted), the snapshot `strategy_fingerprint`,
`strategy_name` (captured at start), and `strategy_deleted`. The `strategies` payload
uses the same `books[]` on each deployment row. `protection_status` is classified from verified
attached-child coverage and venue-visible resting exits, not inferred parent geometry
([ADR 0058](../../../docs/decisions/0058-protection-lifecycle-accounting.md)); an open paper book is
always `covered`, matching its `position_state`
([ADR 0098](../../../docs/decisions/0098-library-views-book-marks-portfolio-fills.md)). Each row also
reports `lifecycle_command` (`none` / `stop_new_entries` / `flatten` / `managed_shutdown`),
breaker latches (`daily_loss_latched`, `drawdown_latched`), optimistic `revision`,
`worker_lease_held` without cash or lease-holder identity, optional `ledger_mark_complete`, and
`open_book_count` without cash or quantities. Latches persist across pause.
`ledger_mark_complete` uses each open product's last journaled close: true when all are marked
or the deployment is flat, false when any close is missing or the journal is unavailable, null
when the books could not be read. It shares the deployment detail's journal mark source; the
separate `performance` report uses market-data closes. Summary reads remain bounded and do not
load historical fills or fetch venue prices.
Default HTTP stop is managed shutdown; flatten is `POST /api/v1/deployments/{id}/stop?flatten=true`
or `thytrader-runtime stop UUID --flatten --confirm`. Latched breakers clear only through
`thytrader-runtime reset-breaker-latches UUID --confirm` /
`POST /api/v1/deployments/{id}/reset-breaker-latches` ([ADR 0064](../../../docs/decisions/0064-deployment-http-lifecycle-and-breaker-latch-reset.md)).
Live capital fields stay on `thytrader-runtime show` / `GET /api/v1/deployments/{id}` as the
`capital` block ([ADR 0065](../../../docs/decisions/0065-deployment-capital-accounting-http.md));
this operator payload still omits cash.

Portfolio exposure and runtime reserved buying power exclude orders with verified non-entry
intent purposes, including paper take-profit limits. Remaining entry quantities still count;
missing intent evidence stays conservatively counted. The manager briefing marks sleeve books
from the decision journal even with zero recent decisions requested, with null marks or fee
fields when their respective evidence is unavailable. Existing response shapes are unchanged.

Sub-hour live (`1m`, `5m`, `15m`, `30m`) pauses when `user_order_feed.state` is not `connected`
and fresh; a pause whose only reason is the feed clears automatically once it is healthy (audit
`user_feed_pause_cleared`). The runtime report adds component `execution_market_data` with
`reason_code` `DEMO_MARKET_DATA` when Coinbase credentials are absent and paper books are active
(synthetic demo candles, not venue prices). Reconciliation `STATE_MISMATCH` findings carry the
deployment `mismatch_detail`; the live `Filled order has no REST fills.` pause also emits
`FILLED_WITHOUT_FILL`, and an ambiguous submit Coinbase never shows keeps `UNKNOWN_ORDERS` plus a
`STATE_MISMATCH` naming the `client_order_id`.
Hour-and-longer live still reconciles through REST. Live fill ingest pages Coinbase List Fills
until the documented `cursor` is exhausted, converts `size_in_quote` to base units, and raises
rather than returning a partial ledger when a row is unparseable, for the wrong product/order, or
missing `trade_time` / `commission` ([ADR 0059](../../../docs/decisions/0059-coinbase-list-fills-cursor-pagination.md)).
Do not treat that failure as “no remaining fills.”

The `risk` payload reports `risk_policy_registry: available` plus policy source, fingerprint, slot caps, allowlist, occupied running and open counts per mode, `daily_loss_limit_fraction`, `max_strategy_drawdown_fraction`, `max_entry_orders_per_minute`, `max_cancellations_per_minute`, `reference_price_collar_fraction`, `allow_intra_strategy_pyramiding`, optional `max_daily_loss_quote` / `max_portfolio_exposure_quote` / `max_venue_order_actions_per_minute` ([ADR 0063](../../../docs/decisions/0063-stage-5-release-discipline-ci-risk-defaults-rate-budget.md)), and pause/mismatch findings. Breaker trips prefix `mismatch_detail` with `DAILY_LOSS_LIMIT` or `STRATEGY_DRAWDOWN_LIMIT` and add a finding with that code. A live deployment still running under the compiled default (only possible from before ADR 0063) adds a `LIVE_RUNNING_ON_COMPILED_DEFAULT_POLICY` finding. The payload omits observed account balances and PnL; policy-configured fractions, integers, the pyramiding boolean, and the operator's own absolute quote caps above are allowed — they are configuration the operator set, not observed exchange balances.

Configuration `payload` includes `yolo_enabled` and `yolo_tiers` (Safe vs YOLO advertisement), plus `settings_file`, `yaml_loaded`, and `yaml_source_of_truth` (always true), and `effective_api_base_url` — the loopback origin agent CLIs resolve for this checkout (`THYTRADER_API_BASE_URL` / settings). Non-secret knobs including YOLO live in `thytrader.yaml` and apply without restart; leftover `THYTRADER_YOLO_TIERS=paper` is valid. Those flags never grant playbook live authority. YOLO `live` may skip `--confirm` on runtime start/pause/resume/stop; live start, live resume, and live place-order still require `--i-understand-live`
(HTTP `i_understand_live: true`). Live place-order, `set-risk-policy`, and `set-settings` still require `--confirm`. It also includes `notify_provider` and `notify_webhook_configured` (boolean only; the webhook URL is never returned).

The `monitor` payload is `thytrader-monitor-v1`: redacted memory status, deployments without cash, recent journals/notifications/why-trade records, and findings (`MEMORY_STORAGE_UNAVAILABLE`, `EXECUTION_UNAVAILABLE`, `DEPLOYMENT_PAUSED`, `DEPLOYMENT_MISMATCH`, `NOTIFICATION_FAILED`). Default-off notify (`provider=none`) is skipped, not failed. YOLO never covers journal or notify writes.

The `trade_reasons` payload is `thytrader-trade-reason-v1` rows: frozen strategy/signal/risk/notes plus ledger facts joined on read. Denied risk with no intent is absent. Notes are attributed; runtime cannot author them.

The `decisions` payload ([ADR 0087](../../../docs/decisions/0087-per-bar-decision-timeline.md)) is
`storage` (`available` / `unavailable`), the echoed `deployment_id` / `strategy_id` / `outcomes`
filters, `decisions[]`, `next_cursor`, `retention_max_rows_per_deployment` (20000), and
`retention_max_age_days` (180). Each row is a `thytrader-bar-decision-v1` record, one per
`(deployment_id, product_id, bar_starts_at)`, identical to `GET /api/v1/deployments/{id}/decisions`:

| Field | Meaning |
|---|---|
| `deployment_id`, `strategy_id`, `strategy_fingerprint`, `product_id`, `timeframe`, `mode` | Which bot, snapshot, product, clock, and `paper`/`live` book |
| `bar_starts_at`, `bar_closes_at`, `evaluated_at` | The completed bar (UTC) and when the worker journaled it |
| `outcome` | `entry_signal` (rule matched; see `action`), `no_signal`, `holding`, `exit`, `entry_blocked` (risk/freshness/sizing refused a matched rule), `skipped` (rule not evaluated), `error` |
| `reason_code`, `summary` | Stable code (`SIGNAL_MATCHED`, `CONDITIONS_NOT_MET`, `HTF_FILTER_NOT_MET`, `HOLDING`, `EXIT_STOP`/`EXIT_TRAIL`/`EXIT_TARGET`/`EXIT_TIME`/`EXIT_FLATTEN`/`EXIT_SIGNAL`, a risk code such as `PRODUCT_NOT_ALLOWLISTED`, a skip code such as `COOLDOWN`, `EVALUATION_ERROR`, `CYCLE_ERROR`) and one human line |
| `skip_reason` | `cooldown`, `max_open_positions`, `warmup`, `pending_entry`, `paused`, `stopped`, `data_gap`, `bar_settling` (only the newest decision candle is within its fixed 120-second publication wait; no entries), `user_feed_gate`, `catch_up`, `entries_disabled`, `entry_geometry`, `entry_sizing` (a matched signal whose stop/target geometry or sizing rested no order; `reason_code` is the exact cause such as `TARGET_NOT_POSITIVE`, ADR 0090), `reference_data_stale` / `reference_data_missing` (a read-only reference instrument had no usable closed bar or warmup, so no entry was attempted; `summary` names the series, ADR 0096), or `null` |
| `exit_reason` | `stop`, `trail`, `target`, `time`, `flatten`, `signal` (`exits.signal_exit`, ADR 0093), or `null` |
| `exit_rule` | Evaluated `exits.signal_exit` rule (`outcome`, `condition` tree) on post-fill bars of an open book; absent or `null` otherwise |
| `action`, `intent_id`, `order_ids`, `orders[]`, `fills[]` | `none` / `intent_created` / `order_submitted` / `order_canceled` / `repriced`, the primary intent, and orders/fills created, changed, or applied in this bar's window (a venue bracket filled between bars belongs to the next bar) |
| `rule` | `outcome` (`matched`/`not_matched`/`undefined`), `entry` tree (`node` `all`/`any`/`not` with `children`, or `comparison` with `label`, `operator`, `operator_symbol`, `left`/`right` operands carrying `label` (reference-instrument operands are prefixed with the reference's base currency and clock, e.g. `BTC · EMA(100) [1d]`, ADR 0096), `value`, `previous_value` for crossovers, and `result` `true`/`false`/`unknown`), optional `htf_filter` (`timeframe`, `outcome`, `condition`), and `signal` (the research `SignalTraceRecord`: `indicator_values[]`). `null` when the rule was not evaluated |
| `risk` | `decision` `allow`/`deny`, `reason_code`, `detail` (risk gate, freshness, or breaker), or `null` |
| `close_price`, `position` | Bar close and the end-of-bar book (`side`, `quantity`, `entry_price`, `stop_price`, `target_price` — `null` when the strategy declares `take_profit: {"kind": "none"}`) |
| `no_trade_bar` | `true` when the bar was a flat zero-volume bar for an interval without trades (ADR 0095), evaluated like any bar; `summary` then ends "(no-trade bar: no trades, flat at the prior close)". `false` otherwise and on records written before ADR 0095 |

Decimals are exact strings. A paused bar's `skip_reason` names the gate (`data_gap`,
`user_feed_gate`) when the pause came from one. Journaling never blocks trading: a failed write is
audited as `decision_journal_write_failed` and that bar is simply absent.

The `studies` payload reports `study_catalog: available|unavailable` plus newest-first catalog rows (`study_fingerprint`, `kind`, product, timeframe, window count, optional selected fingerprint / mean OOS return / stitched flag). It omits child windows and equity curves. Without PostgreSQL, `--local` is `STUDY_CATALOG_UNAVAILABLE` degraded rather than an empty healthy list. Studies do not grant paper or live authority.

The `portfolio` payload matches `GET /api/v1/portfolio`: `as_of`, `demo`, `connection_status`, `permissions`, `total_value.{amount,currency}`, `assets[]`, `unvalued_assets`. It never includes credentials or account identifiers. An asset Coinbase has no `<asset>-USD` market for (dust such as IOTX) is listed in `unvalued_assets` and excluded from `total_value`; the API asks Coinbase once per process, logs one INFO line, and does not log a 404 ERROR on every report (ADR 0094).

The `portfolios` payload (ADR 0088, ADR 0091) lists every portfolio (first 100): `portfolio_storage` (`available` \| `unavailable`), `portfolio_backtest_contract` (`thytrader-portfolio-backtest-v1`), `total`, and per portfolio `portfolio_id`, `name`, `mode` (`paper` \| `live`, fixed), `quote_currency`, `capital_quote`, `cash_reserve_fraction`, `allocated_fraction`, `unallocated_fraction`, `revision`, `sleeves[]` (`sleeve_id`, `strategy_id`, `strategy_name`, `product_id`, `timeframe`, `weight_fraction`, `issues` of `strategy_invalid` \| `quote_currency_mismatch` \| `product_unknown`), `largest_asset` with `largest_asset_weight_fraction` and `largest_asset_within_limit`, `limits`, `manager` (mandate and permissions the manager agent's proposals are bounded by), `deployable` (at least one sleeve and no sleeve issues), `deployment_state` (`not_deployed` \| `running` \| `partially_running` \| `paused` \| `stopped`, ADR 0091), `breaker_latched` with `breaker_reason_code` (`PORTFOLIO_DAILY_LOSS_STOP` \| `PORTFOLIO_DRAWDOWN_STOP` \| null), `pending_proposals`, `latest_backtest` (headline numbers or null), and `active_backtest_jobs`. `paper_live_fill_comparisons[]` (ADR 0097, amended by ADR 0102; at most 10, newest-linked first) compares explicitly linked paper/live strategy books bound to the same `strategy_fingerprint` (sleeves or standalone; no implicit fallback): `strategy_fingerprint`, `strategy_id`, `strategy_name`, `product_id`, and `paper` / `live` digests with `deployment_id`, `portfolio_id` (null when standalone), `status`, `entries_rested`, `entries_filled`, `entries_expired` (canceled unfilled), `entries_rejected`, `entries_working`, `average_fill_vs_limit_bps` (positive is worse than the posted limit), `average_seconds_to_fill`, and `median_seconds_to_fill` (decimal strings or null). Paper waits run to the fill bar's close; live waits end at the venue fill. Component reason codes: `OK`, `PORTFOLIO_BREAKER_LATCHED` (degraded: sleeves stay paused until `thytrader-runtime portfolio-reset-breaker`), `PORTFOLIO_SLEEVE_ISSUES` (degraded), `PORTFOLIO_STORAGE_UNAVAILABLE` (degraded). Read-only; the report cannot deploy or edit a portfolio.

The `fees` payload matches `GET /api/v1/fees`: the Coinbase maker/taker snapshot plus `suggested_*` research/paper defaults. With credentials `suggested_*` equal the account's reported rates (`suggestion_source: coinbase_account`); `schedule_maker_fee_rate` / `schedule_taker_fee_rate` with `suggestion_schedule_tier_id` / `suggestion_schedule_version` / `suggestion_schedule_as_of` are the pinned public band for the same volume, context only (ADR 0090). Demo or missing credentials report `suggestion_source: unavailable` and null suggestions. Suggested rates are modeled defaults, not observed fills.

The `data_catalog` payload lists local verified Parquet datasets joined with the watchlist and worker state for `1h`, `5m`, `15m`, `30m`, `6h`, `1d`, `1m`, `2h`, and `4h`. For a watched row `complete` is watch-relative ([ADR 0095](../../../docs/decisions/0095-sparse-markets-no-trade-bars-listing-floors.md)): the verified series spans the configured lookback (equal to `watch_complete`); unwatched rows keep island completeness. `island_complete` (nullable boolean) is the dataset-level fact (contiguous published bars, `gap_count` 0). `watch_complete` is whether the series spans the configured watch lookback; a 14-day complete island with `lookback_hours: 2160` is not watch-complete, and neither is a two-minute 1m island for a 90-day watch. `watch_covered_candle_count` (nullable integer) counts the lookback window's bars the series covers, out of `watch_expected_candle_count`; `watch_coverage_ratio` (nullable number, four places) is their quotient. `synthetic_no_trade_intervals` (nullable integer) is the newest manifest's count of flat zero-volume bars published for confirmed intervals without trades. Each row also carries `watch_status` (`complete` / `backfilling` / `unknown`), a noun restatement of `watch_complete`: `worker_status=succeeded` describes the latest chunk, never the whole watch. `history_floor_at` (nullable ISO instant) is the market's listing: a backward listing search found no candle at all before it, back to 350 days past the timeframe's lookback ceiling. Coverage starts there and `watch_complete` counts the watch from that floor. Forward walks and interior no-trade gaps never set or move it. `sparsity` is island-only (`none` when the published island has zero gaps). `watch_sparsity` is `gapped` when `watch_complete` is false. Failed worker rows include redacted `failure_code` / `failure_message` matching `GET /api/v1/market-data/ingestion`. Classified missing bars over the watch window are a separate `thytrader-data inspect-gaps` report (`truncated` / `scanned_bar_count` when a server-side budget stopped the scan; [ADR 0072](../../../docs/decisions/0072-catalog-health-bounded-gaps-self-complete-ingest.md)). Dashboard ingestion (`GET /api/v1/market-data/ingestion`) reports the same watch decision. `GET /api/v1/market-data/datasets` lists island fingerprints only. The catalog's dataset side is catalog-grade: each newest revision passes structural checks (manifest facts, content address, file presence, intact Parquet files) from a stat-identity cache, and exact fingerprints are re-verified when a run binds a dataset ([ADR 0085](../../../docs/decisions/0085-fast-research-ingest.md)). `failure_code: provider_rate_limited` means Coinbase throttled the worker and it is backing off. Strategy, paper, live, and discretionary clocks are that same venue set ([ADR 0040](../../../docs/decisions/0040-venue-strategy-paper-live-htf-clocks.md)). Extra catalog timeframes may also back an `htf_filter` dataset when they are a strictly coarser integer multiple of LTF (ADR 0025, ADR 0040, ADR 0041), or an optional per-indicator `timeframe` (ADR 0042). Paper and live evaluate those strategies on last-completed complete-only extra-TF and HTF bars.

Health `components[]` may include `portfolio_history` (worker snapshot freshness). That component is not account balances and not deployment inventory; use the portfolio HTTP routes and runtime `show` respectively ([portfolio-research ops playbook](../../../docs/agent/portfolio-research-ops-playbook.md)).

Health `payload.research_workers` (ADR 0092; `null` without PostgreSQL) reports the research worker pool, the only place backtests, studies, and portfolio backtests run: `configured_workers` (pool size the workers report, `null` until one has), `live_workers` (heartbeated within ~30 s), `queue` (`queued`, `running`, `oldest_queued_at`, `oldest_queued_age_seconds` across both queues), the same four fields split into `research_jobs` (backtests and studies) and `portfolio_backtests`, and `workers[]` per slot: `slot`, `pid`, `state` (`starting` \| `idle` \| `running` \| `stopping`), `live`, `job_id`, `job_kind` (`backtest` \| `study` \| `portfolio_backtest`), `jobs_completed` (since the process started; workers recycle), `rss_bytes`, `started_at`, `heartbeat_at`, `heartbeat_age_seconds`. A `queued` job is waiting for a free worker. The `research_worker` component reason codes: `READY`, `RESEARCH_WORKER_PARTIAL` (some slots silent, degraded), `RESEARCH_WORKER_STALE` (no live worker, degraded), `RESEARCH_WORKER_MISSING` (no worker ever reported, degraded), `RESEARCH_QUEUE_UNAVAILABLE` (queue unreadable, degraded), and `HEARTBEAT_UNAVAILABLE` without PostgreSQL.

Health `payload.ops_contract` names the CLI/API content identity (`id`, `backtest_engine`, paper/live timeframes, `htf_filter_runtimes`, `indicator_timeframe_runtimes`, `indicator_offset_runtimes`, `indicator_operand_offset_runtimes`, `signal_exit_runtimes`, `indicator_kinds`, `position_sides`, `attached_entry_brackets`, `paper_deploy_fee_fields`, `experiential_model_engines`, `risk_breakers`, `order_rate_limits`, `reference_price_collars`, `trade_reason_journals`, `decision_journals`, `multi_instrument_documents`, `intra_strategy_pyramiding`, `lifecycle_commands`, `strategy_model`, `portfolio_model`, `portfolio_modes`, `portfolio_backtest_contract`, `async_backtest_job_statuses`, `research_job_expiry_hours`, `research_worker_pool`, `spot_quote_currencies`, `catalog_health`, `take_profit_kinds`, `live_protection_kinds`, `backtest_diagnostics`, `fee_suggestion_source`, interval cap, expected Alembic revision). `/health/live` and `/health/ready` also return `ops_contract_id`. A missing or unequal contract, or an application version mismatch, means a stale Compose image — rebuild with `make run`. Do not treat HTTP 200 + `0.1.0` as proof the running image matches this checkout.

The `products` payload lists enabled USD, USDC, and USDT spot products from the venue
catalog. Each row carries `product_id`, `base_currency`, `quote_currency`, `trading_enabled`,
`status` (venue status text such as `online`, or `null` when not reported), `alias` (the
product whose order book this one shares, such as `BTC-USD` for `BTC-USDC`, or `null`), and the
order constraints `price_increment`, `base_increment`, `quote_increment`, `base_min_size`, and
`quote_min_size` as exact decimal strings: an order's base size is a multiple of
`base_increment` and at least `base_min_size`, a limit price is a multiple of
`price_increment`, and a quote notional is at least `quote_min_size`. The `indicators` payload lists the 53 implemented kinds only ([ADR 0086](../../../docs/decisions/0086-indicator-catalog-expansion-and-offset.md)), in this order: ema, sma, rsi, atr, volume_sma, highest, lowest, stdev, stdev_sample, roc, williams_r, cci, wma, momentum, mfi, macd, bollinger, stochastic, adx, identity, constant, dema, tema, hma, kama, vwma, supertrend, parabolic_sar, aroon, ichimoku, vortex, linear_regression, trix, stochastic_rsi, ppo, ultimate_oscillator, awesome_oscillator, cmo, tsi, keltner, donchian, bollinger_percent_b, bollinger_bandwidth, natr, choppiness, historical_volatility, obv, cmf, accumulation_distribution, vwap, force_index, zscore, percent_rank. Each row carries `kind`, `label`, `category` (`trend`, `momentum`, `volatility`, `volume`, `statistical`, or `price`), a one-line `summary`, `inputs`, `input_mode` (`configurable`: author picks one of open/high/low/close/volume; `locked`: exactly `inputs` in that order; `none`: omit input), `default_input`, `parameter_kind`, `period_min`/`period_max` (bounds of the required integer parameters), `parameters` (each `name`, `label`, `value_type` `integer`/`decimal`, `minimum`, `maximum`, `exclusive_minimum`, builder `default`, `optional`, and one-line `help`; decimals are strings), `constraints` (ordering rules such as `fast_period < slow_period`), `outputs` (series names for multi-series kinds; empty for single-output), `warmup` (formula in parameter names), `default_warmup_bars`, `supports_timeframe`, and `supports_offset` (false only for `constant`). `parameter_kind` is one of `period`, `none`, `value`, `macd` (MACD and PPO), `bollinger` (Bollinger, %B, bandwidth), `stochastic`, `kama`, `supertrend`, `parabolic_sar`, `ichimoku`, `stochastic_rsi`, `ultimate_oscillator`, `awesome_oscillator`, `tsi`, `keltner`, `historical_volatility`, or `signal` (OBV and A/D). Unlisted kinds are not present. Optional per-indicator `timeframe` and bar-lag `offset` are strategy-document fields, not catalog rows; health `ops_contract.indicator_timeframe_runtimes` and `ops_contract.indicator_offset_runtimes` name research, paper, and live.

The support-bundle `payload` nests the other reports unchanged (it does not nest `runtime`, `data_catalog`, `products`, `indicators`, or `studies`).

The committed JSON Schema is [operator-report-v1.schema.json](operator-report-v1.schema.json). Run `uv run thytrader-operator schema-check` to verify skill docs against `SCHEMA_VERSION`.

Decision schema shape is unchanged by ADR 0106. Protective maintenance with inventory is
`outcome: holding`, not a canceled `pending_entry`; linked order purposes/statuses explain the
replacement. Existing historical decisions are not rewritten. Exposure verdict `detail` includes
exact Decimal existing exposure, proposed notional, effective cap, and account capital; allocation
denials identify the reservation. `BREAKER_MARK_MISSING` retains its code but identifies the
deployment and whether inventory marks or equity/day-open baselines are missing. These are risk
calculation diagnostics, not a list of account balances. The operator `risk` payload remains
unchanged and continues omitting balances.

Indicator operands may independently add `offset` (0–500; ADR 0099), including multi-series reads. Health `ops_contract.indicator_operand_offset_runtimes` names research, paper, and live. Lagged signal values use `id@N` / `id.series@N`; original output keys stay present. Rule labels show declaration plus operand lag on the indicator's native clock; missing lagged history remains unknown.

Ops contract v61 adds `portfolio_sleeve_operations: ["batch_add", "create_with_sleeves"]`:
optional initial sleeves on portfolio creation are validated and persisted atomically at revision 1
([ADR 0101](../../../docs/decisions/0101-atomic-portfolio-creation-with-sleeves.md)). This is a
definition mutation in the confirmed portfolio lane and grants no runtime authority.

Ops contract v60 added `runtime_observability: fee_adjusted_book_pnl`. Deployment detail
(`thytrader-runtime show`) position rows, including compatibility `position`, and portfolio
deployment (`thytrader-portfolio deployment`) sleeve `books[]` add nullable decimal strings:
`entry_fees` for paid fees allocated to held inventory and `unrealized_pnl_net` for gross
`unrealized_pnl` minus those fees. Future exit fees are excluded. Null means no mark or
unverified fill evidence (missing/mismatched or above the 1000 applied-fill window cap), not zero.
Partial exits allocate fees proportionally; adds accumulate them. Operator aggregate ledger
fields keep their existing semantics. See [ADR 0100](../../../docs/decisions/0100-fee-adjusted-open-book-pnl.md).


Ops contract v62 adds `runtime_observability: explicit_deployment_twins` and expects Alembic
`0060`. Existing comparison fields are unchanged, but each row represents a saved pair. Multiple
rows may have the same fingerprint: identify a pair by its paper and live deployment ids.
`GET /api/v1/deployments/{id}/twin` returns `{deployment_id, twin}`; `twin` is null or an object
with UUID `paper_deployment_id`, UUID `live_deployment_id`, and UTC timestamp `linked_at`.
Link/unlink are confirmed runtime metadata controls; they confer no trading authority.

Ops contract v63 adds `async_study_planning: "worker"`, `newest_bar_settle_seconds: 120`, and
`origin_counts` in `strategy_library`. Strategy-library HTTP pages add `origin_counts` with
nonnegative `operator`, `research`, `all` counts after the tag filter and before origin filtering
or pagination. Async 202 echoes stay unchanged; planning failure appears on the durable job.

For rule-equivalent clone twins (ADR 0105), each `paper` / `live` fill digest adds its actual
`strategy_fingerprint` (nullable when unavailable). The compatibility top-level fingerprint is
the paper side's identity; it does not claim the two identities match. The server verifies pinned
rules before storing an explicit link; report selection remains persisted-link-only.
Ops contract v63 advertises `runtime_observability: rule_matched_deployment_twins`.
