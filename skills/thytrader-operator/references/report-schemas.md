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

The `data_catalog` payload lists local verified Parquet datasets joined with the watchlist and worker state for `1h`, `5m`, and `15m`. `complete` is island completeness (contiguous published bars, `gap_count` 0). `watch_complete` is whether that island spans the configured watch lookback; a 14-day complete island with `lookback_hours: 2160` is not watch-complete. Classified missing bars over the watch window are a separate `thytrader-data inspect-gaps` report. Strategy, paper, and live clocks remain `1h` or `5m` (live `1h`).

Health `payload.ops_contract` names the CLI/API content identity (`id`, engines, paper/live timeframes, interval cap, expected Alembic revision). `/health/live` and `/health/ready` also return `ops_contract_id`. A missing or unequal contract, or an application version mismatch, means a stale Compose image — rebuild with `make run`. Do not treat HTTP 200 + `0.1.0` as proof the running image matches this checkout.

The `products` payload lists enabled USD spot products. The `indicators` payload lists implemented kinds only: ema, sma, rsi, atr, volume_sma.

The support-bundle `payload` nests the other reports unchanged (it does not nest `runtime`, `data_catalog`, `products`, or `indicators`).

The committed JSON Schema is [operator-report-v1.schema.json](operator-report-v1.schema.json). Run `uv run thytrader-operator schema-check` to verify skill docs against `SCHEMA_VERSION`.
