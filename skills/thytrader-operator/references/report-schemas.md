# Operator report schema

Every JSON report includes:

- `schema_version`: `thytrader-operator-report-v1`
- `report_kind`: `health` \| `configuration` \| `exchange` \| `market_data` \| `data_catalog` \| `products` \| `indicators` \| `strategies` \| `performance` \| `risk` \| `reconciliation` \| `runtime` \| `support_bundle`
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

The `runtime` payload lists deployment identities plus risk and reconciliation findings. It omits cash, quantities, and order payloads.

The `data_catalog` payload lists local verified Parquet datasets joined with the watchlist and worker state for `1h`, `5m`, `15m`, `30m`, `6h`, and `1d`. `complete` is island completeness (contiguous published bars, `gap_count` 0). `watch_complete` is whether that island spans the configured watch lookback; a 14-day complete island with `lookback_hours: 2160` is not watch-complete. `sparsity` is `gapped` when `watch_complete` is false, even if the island itself has zero gaps. Classified missing bars over the watch window are a separate `thytrader-data inspect-gaps` report. Dashboard ingestion (`GET /api/v1/market-data/ingestion`) reports the same watch decision. `GET /api/v1/market-data/datasets` lists island fingerprints only. Strategy, paper, and live clocks remain `1h` or `5m` (live `1h`). Extra catalog timeframes may back a research `htf_filter` dataset (ADR 0025); paper and live still reject those strategies.

Health `payload.ops_contract` names the CLI/API content identity (`id`, engines, paper/live timeframes, interval cap, expected Alembic revision). `/health/live` and `/health/ready` also return `ops_contract_id`. A missing or unequal contract, or an application version mismatch, means a stale Compose image — rebuild with `make run`. Do not treat HTTP 200 + `0.1.0` as proof the running image matches this checkout.

The `products` payload lists enabled USD spot products. The `indicators` payload lists implemented kinds only: ema, sma, rsi, atr, volume_sma, highest, lowest, stdev, roc, williams_r, cci, wma, momentum, mfi, macd, bollinger, identity, constant. Highest is locked to high, lowest to low, stdev, roc, wma, momentum, macd, and bollinger to close. Williams %R and CCI lock high/low/close like ATR. MFI locks high/low/close/volume. Identity selects one of open/high/low/close/volume. Constant omits input and declares value. Each row includes `parameter_kind` (`period`, `none`, `value`, `macd`, or `bollinger`). Multi-series kinds list `outputs` (`macd`/`signal`/`histogram` or `middle`/`upper`/`lower`). Stochastic, ADX, and other unlisted kinds are not present.

The support-bundle `payload` nests the other reports unchanged (it does not nest `runtime`, `data_catalog`, `products`, or `indicators`).

The committed JSON Schema is [operator-report-v1.schema.json](operator-report-v1.schema.json). Run `uv run thytrader-operator schema-check` to verify skill docs against `SCHEMA_VERSION`.
