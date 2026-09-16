# Operator report schema

Every JSON report includes:

- `schema_version`: `thytrader-operator-report-v1`
- `report_kind`: `health` \| `configuration` \| `exchange` \| `market_data` \| `data_catalog` \| `products` \| `indicators` \| `strategies` \| `performance` \| `risk` \| `reconciliation` \| `runtime` \| `monitor` \| `studies` \| `trade_reasons` \| `support_bundle`
- `application_version`: ThyTrader package version
- `generated_at`: timezone-aware UTC timestamp
- `timezone`: `UTC`
- `overall_status`: `healthy` \| `degraded` \| `failed`
- `components[]`: `name`, `status`, `reason_code`, `detail`
- `redaction`: `secrets_redacted`, `raw_environment_omitted`, `account_identifiers_omitted`, `balances_omitted` (all true)
- `partial_result_warnings[]`
- `recommended_next_action`
- `payload`: report-specific object

`reason_code` matches `^[A-Z][A-Z0-9_]{0,63}$`.

Performance `payload.mode` is `backtest`, `paper`, or `live`. Backtest metrics come from an immutable result. Paper/live metrics come from a fill ledger: `trade_count` is round trips, `total_net_pnl` / return / drawdown use recorded fills plus a last-close mark for open inventory. Open inventory without a mark leaves `total_net_pnl` null (`MISSING_MARK`) instead of inventing equity. Drawdown is fill-event marks, not a bar equity curve.

The `runtime` payload lists deployment identities plus risk and reconciliation findings. It also
reports `user_order_feed` lifecycle state (`connected` / `stale` / `disabled`, timestamps) without
JWT material or order payloads. It omits cash, quantities, and order payloads. Each deployment
includes `kind` (`strategy` or `discretionary`) and optional strategy identity.

Sub-hour live (`1m`, `5m`, `15m`, `30m`) pauses when `user_order_feed.state` is not `connected`.
Hour-and-longer live still reconciles through REST.

The `risk` payload reports `risk_policy_registry: available` plus policy source, fingerprint, slot caps, allowlist, occupied running and open counts per mode, `daily_loss_limit_fraction`, `max_strategy_drawdown_fraction`, `max_entry_orders_per_minute`, `max_cancellations_per_minute`, `reference_price_collar_fraction`, and pause/mismatch findings. Breaker trips prefix `mismatch_detail` with `DAILY_LOSS_LIMIT` or `STRATEGY_DRAWDOWN_LIMIT` and add a finding with that code. The payload omits account balances and dollar amounts; fractions and integers are allowed.

Configuration `payload` includes `yolo_enabled` and `yolo_tiers` (Safe vs YOLO advertisement), plus `settings_file`, `yaml_loaded`, and `yaml_source_of_truth` (always true). Non-secret knobs including YOLO live in `thytrader.yaml` and apply without restart; leftover `THYTRADER_YOLO_TIERS=paper` is valid. Those flags never grant playbook live authority. YOLO `live` may skip `--confirm` on runtime start/pause/resume/stop; live start still requires `--i-understand-live`. Live place-order, `set-risk-policy`, and `set-settings` still require `--confirm`. It also includes `notify_provider` and `notify_webhook_configured` (boolean only; the webhook URL is never returned).

The `monitor` payload is `thytrader-monitor-v1`: redacted memory status, deployments without cash, recent journals/notifications/why-trade records, and findings (`MEMORY_STORAGE_UNAVAILABLE`, `EXECUTION_UNAVAILABLE`, `DEPLOYMENT_PAUSED`, `DEPLOYMENT_MISMATCH`, `NOTIFICATION_FAILED`). Default-off notify (`provider=none`) is skipped, not failed. YOLO never covers journal or notify writes.

The `trade_reasons` payload is `thytrader-trade-reason-v1` rows: frozen strategy/signal/risk/notes plus ledger facts joined on read. Denied risk with no intent is absent. Notes are attributed; runtime cannot author them.

The `studies` payload reports `study_catalog: available|unavailable` plus newest-first catalog rows (`study_fingerprint`, `kind`, product, timeframe, window count, optional selected fingerprint / mean OOS return / stitched flag). It omits child windows and equity curves. Without PostgreSQL, `--local` is `STUDY_CATALOG_UNAVAILABLE` degraded rather than an empty healthy list. Studies do not grant paper or live authority.

The `data_catalog` payload lists local verified Parquet datasets joined with the watchlist and worker state for `1h`, `5m`, `15m`, `30m`, `6h`, `1d`, `1m`, `2h`, and `4h`. `complete` is island completeness (contiguous published bars, `gap_count` 0). `watch_complete` is whether that island spans the configured watch lookback; a 14-day complete island with `lookback_hours: 2160` is not watch-complete. `sparsity` is `gapped` when `watch_complete` is false, even if the island itself has zero gaps. Classified missing bars over the watch window are a separate `thytrader-data inspect-gaps` report. Dashboard ingestion (`GET /api/v1/market-data/ingestion`) reports the same watch decision. `GET /api/v1/market-data/datasets` lists island fingerprints only. Strategy, paper, live, and discretionary clocks are that same venue set ([ADR 0040](../../../docs/decisions/0040-venue-strategy-paper-live-htf-clocks.md)). Extra catalog timeframes may also back an `htf_filter` dataset when they are a strictly coarser integer multiple of LTF (ADR 0025, ADR 0040, ADR 0041), or an optional per-indicator `timeframe` (ADR 0042). Paper and live evaluate those strategies on last-completed complete-only extra-TF and HTF bars.

Health `payload.ops_contract` names the CLI/API content identity (`id`, engines, paper/live timeframes, `htf_filter_runtimes`, `indicator_timeframe_runtimes`, `position_sides`, `attached_entry_brackets`, `paper_deploy_fee_fields`, `experiential_model_engines`, `risk_breakers`, `order_rate_limits`, `reference_price_collars`, `trade_reason_journals`, interval cap, expected Alembic revision). `/health/live` and `/health/ready` also return `ops_contract_id`. A missing or unequal contract, or an application version mismatch, means a stale Compose image — rebuild with `make run`. Do not treat HTTP 200 + `0.1.0` as proof the running image matches this checkout.

The `products` payload lists enabled USD spot products. The `indicators` payload lists implemented kinds only: ema, sma, rsi, atr, volume_sma, highest, lowest, stdev, stdev_sample, roc, williams_r, cci, wma, momentum, mfi, macd, bollinger, stochastic, adx, identity, constant. Configurable rolling kinds (ema, sma, wma, highest, lowest, stdev, stdev_sample, roc, momentum) list inputs `open`/`high`/`low`/`close`/`volume`. RSI stays locked to close, volume SMA to volume, MACD and Bollinger to close. Williams %R, CCI, stochastic, and ADX lock high/low/close like ATR. MFI locks high/low/close/volume. Identity selects one of open/high/low/close/volume. Constant omits input and declares value. Each row includes `parameter_kind` (`period`, `none`, `value`, `macd`, `bollinger`, or `stochastic`). Multi-series kinds list `outputs` (`macd`/`signal`/`histogram`, `middle`/`upper`/`lower`, `k`/`d`, or `adx`/`plus_di`/`minus_di`). Unlisted kinds are not present. Optional per-indicator `timeframe` is a strategy-document field, not a catalog row; health `ops_contract.indicator_timeframe_runtimes` names research, paper, and live.

The support-bundle `payload` nests the other reports unchanged (it does not nest `runtime`, `data_catalog`, `products`, `indicators`, or `studies`).

The committed JSON Schema is [operator-report-v1.schema.json](operator-report-v1.schema.json). Run `uv run thytrader-operator schema-check` to verify skill docs against `SCHEMA_VERSION`.
