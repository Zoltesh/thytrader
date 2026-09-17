# Operator report schema

Every JSON report includes:

- `schema_version`: `thytrader-operator-report-v1`
- `report_kind`: `health` \| `configuration` \| `exchange` \| `market_data` \| `data_catalog` \| `products` \| `indicators` \| `strategies` \| `performance` \| `risk` \| `reconciliation` \| `runtime` \| `monitor` \| `studies` \| `trade_reasons` \| `support_bundle`
- `application_version`: ThyTrader package version
- `generated_at`: timezone-aware UTC timestamp
- `timezone`: `UTC`
- `overall_status`: `healthy` \| `degraded` \| `failed`
- `components[]`: `name`, `status`, `reason_code`, `detail`
- `redaction`: `secrets_redacted`, `raw_environment_omitted`, `account_identifiers_omitted`, `balances_omitted` (all true). `balances_omitted` means this operator report never returns observed exchange or demo account balances — not that balances are unavailable elsewhere. Account holdings live on `GET /api/v1/portfolio` (no operator CLI today). Deployment quantities live on `thytrader-runtime show` / `GET /api/v1/deployments/{id}`.
- `partial_result_warnings[]`
- `recommended_next_action`
- `payload`: report-specific object

`reason_code` matches `^[A-Z][A-Z0-9_]{0,63}$`.

Performance `payload.mode` is `backtest`, `paper`, or `live`. Backtest metrics come from an immutable result. Paper/live metrics come from a fill ledger: `trade_count` is round trips, `total_net_pnl` / return / drawdown use recorded fills plus a last-close mark for open inventory. Open inventory without a mark leaves `total_net_pnl` null (`MISSING_MARK`) instead of inventing equity. Drawdown is fill-event marks, not a bar equity curve.

The `runtime` payload lists deployment identities plus risk and reconciliation findings. It also
reports `user_order_feed` lifecycle state (`connected` / `stale` / `disabled`, timestamps) without
JWT material or order payloads. It omits cash, quantities, and order payloads. Each deployment
includes `kind` (`strategy` or `discretionary`) and optional strategy identity. Multi-instrument
deployments add `books[]`: `product_id`, `phase`, `side` (`null` when flat), and
`protection_status` (`flat` \| `covered` \| `unprotected` \| `unknown`). No quantities. Do not
treat the deployment-level `product_id` as the only open book
([ADR 0060](../../../docs/decisions/0060-multi-book-deployment-api.md)). The `strategies` payload
uses the same `books[]` on each deployment row. `protection_status` is classified from verified
attached-child coverage and venue-visible resting exits, not inferred parent geometry
([ADR 0058](../../../docs/decisions/0058-protection-lifecycle-accounting.md)). Each row also
reports `lifecycle_command` (`none` / `stop_new_entries` / `flatten` / `managed_shutdown`),
breaker latches (`daily_loss_latched`, `drawdown_latched`), optimistic `revision`, and
`worker_lease_held` without cash or lease-holder identity. Latches persist across pause.
Default HTTP stop is managed shutdown; flatten is `POST /api/v1/deployments/{id}/stop?flatten=true`
or `thytrader-runtime stop UUID --flatten --confirm`. Live capital fields stay on
`thytrader-runtime show` / `GET /api/v1/deployments/{id}` as the `capital` block
([ADR 0065](../../../docs/decisions/0065-deployment-capital-accounting-http.md)); this operator
payload still omits cash.

Sub-hour live (`1m`, `5m`, `15m`, `30m`) pauses when `user_order_feed.state` is not `connected`.
Hour-and-longer live still reconciles through REST. Live fill ingest pages Coinbase List Fills
until the documented `cursor` is exhausted, converts `size_in_quote` to base units, and raises
rather than returning a partial ledger when a row is unparseable, for the wrong product/order, or
missing `trade_time` / `commission` ([ADR 0059](../../../docs/decisions/0059-coinbase-list-fills-cursor-pagination.md)).
Do not treat that failure as “no remaining fills.”

The `risk` payload reports `risk_policy_registry: available` plus policy source, fingerprint, slot caps, allowlist, occupied running and open counts per mode, `daily_loss_limit_fraction`, `max_strategy_drawdown_fraction`, `max_entry_orders_per_minute`, `max_cancellations_per_minute`, `reference_price_collar_fraction`, `allow_intra_strategy_pyramiding`, optional `max_daily_loss_quote` / `max_portfolio_exposure_quote` / `max_venue_order_actions_per_minute` ([ADR 0063](../../../docs/decisions/0063-stage-5-release-discipline-ci-risk-defaults-rate-budget.md)), and pause/mismatch findings. Breaker trips prefix `mismatch_detail` with `DAILY_LOSS_LIMIT` or `STRATEGY_DRAWDOWN_LIMIT` and add a finding with that code. A live deployment still running under the compiled default (only possible from before ADR 0063) adds a `LIVE_RUNNING_ON_COMPILED_DEFAULT_POLICY` finding. The payload omits observed account balances and PnL; policy-configured fractions, integers, the pyramiding boolean, and the operator's own absolute quote caps above are allowed — they are configuration the operator set, not observed exchange balances.

Configuration `payload` includes `yolo_enabled` and `yolo_tiers` (Safe vs YOLO advertisement), plus `settings_file`, `yaml_loaded`, and `yaml_source_of_truth` (always true). Non-secret knobs including YOLO live in `thytrader.yaml` and apply without restart; leftover `THYTRADER_YOLO_TIERS=paper` is valid. Those flags never grant playbook live authority. YOLO `live` may skip `--confirm` on runtime start/pause/resume/stop; live start still requires `--i-understand-live`. Live place-order, `set-risk-policy`, and `set-settings` still require `--confirm`. It also includes `notify_provider` and `notify_webhook_configured` (boolean only; the webhook URL is never returned).

The `monitor` payload is `thytrader-monitor-v1`: redacted memory status, deployments without cash, recent journals/notifications/why-trade records, and findings (`MEMORY_STORAGE_UNAVAILABLE`, `EXECUTION_UNAVAILABLE`, `DEPLOYMENT_PAUSED`, `DEPLOYMENT_MISMATCH`, `NOTIFICATION_FAILED`). Default-off notify (`provider=none`) is skipped, not failed. YOLO never covers journal or notify writes.

The `trade_reasons` payload is `thytrader-trade-reason-v1` rows: frozen strategy/signal/risk/notes plus ledger facts joined on read. Denied risk with no intent is absent. Notes are attributed; runtime cannot author them.

The `studies` payload reports `study_catalog: available|unavailable` plus newest-first catalog rows (`study_fingerprint`, `kind`, product, timeframe, window count, optional selected fingerprint / mean OOS return / stitched flag). It omits child windows and equity curves. Without PostgreSQL, `--local` is `STUDY_CATALOG_UNAVAILABLE` degraded rather than an empty healthy list. Studies do not grant paper or live authority.

The `data_catalog` payload lists local verified Parquet datasets joined with the watchlist and worker state for `1h`, `5m`, `15m`, `30m`, `6h`, `1d`, `1m`, `2h`, and `4h`. `complete` is island completeness (contiguous published bars, `gap_count` 0). `watch_complete` is whether that island spans the configured watch lookback; a 14-day complete island with `lookback_hours: 2160` is not watch-complete. `sparsity` is `gapped` when `watch_complete` is false, even if the island itself has zero gaps. Classified missing bars over the watch window are a separate `thytrader-data inspect-gaps` report. Dashboard ingestion (`GET /api/v1/market-data/ingestion`) reports the same watch decision. `GET /api/v1/market-data/datasets` lists island fingerprints only. Strategy, paper, live, and discretionary clocks are that same venue set ([ADR 0040](../../../docs/decisions/0040-venue-strategy-paper-live-htf-clocks.md)). Extra catalog timeframes may also back an `htf_filter` dataset when they are a strictly coarser integer multiple of LTF (ADR 0025, ADR 0040, ADR 0041), or an optional per-indicator `timeframe` (ADR 0042). Paper and live evaluate those strategies on last-completed complete-only extra-TF and HTF bars.

Health `components[]` may include `portfolio_history` (worker snapshot freshness). That component is not account balances and not deployment inventory; use the portfolio HTTP routes and runtime `show` respectively ([portfolio-research ops playbook](../../../docs/agent/portfolio-research-ops-playbook.md)).

Health `payload.ops_contract` names the CLI/API content identity (`id`, engines, paper/live timeframes, `htf_filter_runtimes`, `indicator_timeframe_runtimes`, `position_sides`, `attached_entry_brackets`, `paper_deploy_fee_fields`, `experiential_model_engines`, `risk_breakers`, `order_rate_limits`, `reference_price_collars`, `trade_reason_journals`, `multi_instrument_documents`, `intra_strategy_pyramiding`, `lifecycle_commands`, interval cap, expected Alembic revision). `/health/live` and `/health/ready` also return `ops_contract_id`. A missing or unequal contract, or an application version mismatch, means a stale Compose image — rebuild with `make run`. Do not treat HTTP 200 + `0.1.0` as proof the running image matches this checkout.

The `products` payload lists enabled USD spot products. The `indicators` payload lists implemented kinds only: ema, sma, rsi, atr, volume_sma, highest, lowest, stdev, stdev_sample, roc, williams_r, cci, wma, momentum, mfi, macd, bollinger, stochastic, adx, identity, constant. Configurable rolling kinds (ema, sma, wma, highest, lowest, stdev, stdev_sample, roc, momentum) list inputs `open`/`high`/`low`/`close`/`volume`. RSI stays locked to close, volume SMA to volume, MACD and Bollinger to close. Williams %R, CCI, stochastic, and ADX lock high/low/close like ATR. MFI locks high/low/close/volume. Identity selects one of open/high/low/close/volume. Constant omits input and declares value. Each row includes `parameter_kind` (`period`, `none`, `value`, `macd`, `bollinger`, or `stochastic`). Multi-series kinds list `outputs` (`macd`/`signal`/`histogram`, `middle`/`upper`/`lower`, `k`/`d`, or `adx`/`plus_di`/`minus_di`). Unlisted kinds are not present. Optional per-indicator `timeframe` is a strategy-document field, not a catalog row; health `ops_contract.indicator_timeframe_runtimes` names research, paper, and live.

The support-bundle `payload` nests the other reports unchanged (it does not nest `runtime`, `data_catalog`, `products`, `indicators`, or `studies`).

The committed JSON Schema is [operator-report-v1.schema.json](operator-report-v1.schema.json). Run `uv run thytrader-operator schema-check` to verify skill docs against `SCHEMA_VERSION`.
