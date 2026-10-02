# Operator diagnostics API

All routes are `GET` under `/api/v1/operator`. They share application services with `thytrader-operator` and do not mutate orders, strategies, or deployments.

Base URL: the loopback API origin of this install. The CLI resolves its base URL from `--base-url`, then `THYTRADER_API_BASE_URL`, then the
`THYTRADER_API_HOST` / `THYTRADER_API_PORT` settings (the same `.env` Compose reads; the default
port is `8200`, but installs may override it, so never hard-code a port). For raw `curl`, export
`THYTRADER_API_BASE_URL` and call `"$THYTRADER_API_BASE_URL/api/v1/..."`. `--local` is an explicit store-backed alternative, not an automatic fallback.

| Method | Path | Report kind |
|---|---|---|
| GET | `/api/v1/operator/health` | `health` |
| GET | `/api/v1/operator/configuration` | `configuration` |
| GET | `/api/v1/operator/exchange` | `exchange` |
| GET | `/api/v1/operator/market-data` | `market_data` |
| GET | `/api/v1/operator/data-catalog` | `data_catalog` |
| GET | `/api/v1/operator/products` | `products` |
| GET | `/api/v1/operator/indicators` | `indicators` |
| GET | `/api/v1/operator/strategies` | `strategies` |
| GET | `/api/v1/operator/performance` | `performance` |
| GET | `/api/v1/operator/risk` | `risk` |
| GET | `/api/v1/operator/reconciliation` | `reconciliation` |
| GET | `/api/v1/operator/runtime` | `runtime` |
| GET | `/api/v1/operator/monitor` | `monitor` |
| GET | `/api/v1/operator/studies` | `studies` |
| GET | `/api/v1/operator/trade-reasons` | `trade_reasons` |
| GET | `/api/v1/operator/decisions` | `decisions` |
| GET | `/api/v1/operator/support-bundle` | `support_bundle` |
| GET | `/api/v1/operator/portfolio` | `portfolio` |
| GET | `/api/v1/operator/fees` | `fees` |
| GET | `/api/v1/operator/portfolios` | `portfolios` |

Query parameters:

- `market-data`: optional `product_id` matching `^[A-Z0-9]{2,20}-(?:USD|USDC)$`, optional `timeframe` (`1h`, `5m`, `15m`, `30m`, `6h`, `1d`, `1m`, `2h`, or `4h`, default `1h`)
- `performance`: optional `result_fingerprint` (`sha256:` + 64 lowercase hex) or `deployment_id` (UUID)
- `runtime`: optional `deployment_id` (UUID)
- `trade-reasons`: optional `intent_id` (UUID) and/or `deployment_id` (UUID)
- `decisions`: optional `deployment_id` (UUID) or `strategy_id` (UUID) (neither pages every bot), repeated `outcome` (`entry_signal`, `no_signal`, `holding`, `exit`, `entry_blocked`, `skipped`, `error`), `limit` 1..200 (default 50), and `cursor` (the previous page's `payload.next_cursor`)

HTTP `200` means the diagnostics document was produced. Judge instance health from `overall_status`, not from the HTTP status code.

Worker components use PostgreSQL heartbeats (`portfolio_worker`, `market_data_worker`,
`execution_worker`). Docker `/tmp` readiness files are not operator health. Database health is an
engine ping when `THYTRADER_DATABASE_URL` is set (`DATABASE_UNCONFIGURED`, `DATABASE_ENGINE_MISSING`,
or `DATABASE_UNREACHABLE`).

If the CLI exits because of an application version mismatch, an ops-contract mismatch, or an agent route returns 404 while `GET /health/ready` is 200, rebuild with `make run`. Package version `0.1.0` is not enough to prove the running image matches this CLI. Do not treat a printed report plus a stderr warning as success.

CLI equivalents are listed in `SKILL.md`. Process entry point: `thytrader-operator`.
