# Operator diagnostics API

All routes are `GET` under `/api/v1/operator`. They share application services with `thytrader-operator` and do not mutate orders, strategies, or deployments.

Base URL for the supported local stack: `http://127.0.0.1:8200`. The CLI defaults to that origin (`THYTRADER_API_BASE_URL` or settings). `--local` is an explicit store-backed alternative, not an automatic fallback.

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
| GET | `/api/v1/operator/support-bundle` | `support_bundle` |

Query parameters:

- `market-data`: optional `product_id` matching `^[A-Z0-9]{2,20}-USD$`, optional `timeframe` (`1h` or `5m`, default `1h`)
- `performance`: optional `result_fingerprint` (`sha256:` + 64 lowercase hex) or `deployment_id` (UUID)
- `runtime`: optional `deployment_id` (UUID)

HTTP `200` means the diagnostics document was produced. Judge instance health from `overall_status`, not from the HTTP status code.

Worker components use PostgreSQL heartbeats (`portfolio_worker`, `market_data_worker`,
`execution_worker`). Docker `/tmp` readiness files are not operator health. Database health is an
engine ping when `THYTRADER_DATABASE_URL` is set (`DATABASE_UNCONFIGURED`, `DATABASE_ENGINE_MISSING`,
or `DATABASE_UNREACHABLE`).

If the CLI stderr reports an application version mismatch, an ops-contract mismatch, or an agent route returns 404 while `GET /health/ready` is 200, rebuild with `make run`. Package version `0.1.0` is not enough to prove the running image matches this CLI.

CLI equivalents are listed in `SKILL.md`. Process entry point: `thytrader-operator`.
