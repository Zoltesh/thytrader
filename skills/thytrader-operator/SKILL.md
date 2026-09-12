---
name: thytrader-operator
description: >-
  Diagnose a running ThyTrader instance through the versioned read-only operator
  CLI and HTTP API. Use when checking health, configuration, Coinbase connectivity,
  market-data freshness, strategy/runtime status, backtest or paper/live performance,
  reconciliation, or a redacted support bundle. Never places, edits, or cancels
  orders and never arms live trading.
---

# ThyTrader operator

Read-only diagnostics for a running instance. Do not scrape logs, query PostgreSQL, or import private internals.

Schema version: `thytrader-operator-report-v1` (`schema_version` on every JSON report).

Default transport is the loopback HTTP API (`THYTRADER_API_BASE_URL` or `http://127.0.0.1:8200`). Pass `--local` only when you intentionally want process stores instead of HTTP. Do not fall back from HTTP to PostgreSQL if the API is down.

JSON is the default CLI output. Do not add `--format json` to every command.

## Hard stop

When operating a running instance, do not edit `src/`, `compose.yaml`, Dockerfiles, Alembic, or tests.
Do not search the tree for a code patch. Report failures through this skill. Rebuild or restart only
with `make run` when the user asked to rebuild, or when health/HTTP says the Compose image is stale
(version mismatch, ops-contract mismatch, or 404 on agent routes while `/health/ready` is 200). Open the `ops/` workspace
instead of the git root. Run every `uv run thytrader-*` command from the repository root (the parent
of `ops/`).

## Commands

Prefer the CLI. HTTP is the same contract on loopback.

| Need | CLI | HTTP |
|---|---|---|
| Health | `uv run thytrader-operator health` | `GET /api/v1/operator/health` |
| Configuration | `uv run thytrader-operator configuration` | `GET /api/v1/operator/configuration` |
| Exchange | `uv run thytrader-operator exchange` | `GET /api/v1/operator/exchange` |
| Market data | `uv run thytrader-operator market-data [--product-id BTC-USD] [--timeframe 1h\|5m]` | `GET /api/v1/operator/market-data` |
| Data catalog | `uv run thytrader-operator data-catalog` | `GET /api/v1/operator/data-catalog` |
| Products | `uv run thytrader-operator products` | `GET /api/v1/operator/products` |
| Indicators | `uv run thytrader-operator indicators` | `GET /api/v1/operator/indicators` |
| Strategies / runtimes | `uv run thytrader-operator strategies` | `GET /api/v1/operator/strategies` |
| Runtime watch | `uv run thytrader-operator runtime [--deployment-id UUID]` | `GET /api/v1/operator/runtime` |
| Performance | `uv run thytrader-operator performance --result-fingerprint sha256:…` or `--deployment-id UUID` | `GET /api/v1/operator/performance` |
| Risk | `uv run thytrader-operator risk` | `GET /api/v1/operator/risk` |
| Reconciliation | `uv run thytrader-operator reconciliation` | `GET /api/v1/operator/reconciliation` |
| Support bundle | `uv run thytrader-operator support-bundle` | `GET /api/v1/operator/support-bundle` |
| Schema check | `uv run thytrader-operator schema-check` | (local files only) |

`--format text` is a short summary. Parent flags such as `--format` may follow the subcommand.

Machine-readable envelope: [operator-report-v1.schema.json](references/operator-report-v1.schema.json).

## Exit codes

- `0` overall `healthy` (schema-check success is also `0`)
- `1` overall `degraded`
- `2` overall `failed` (schema-check mismatch is also `2`)
- argparse usage errors use the interpreter's usual non-zero code

Missing telemetry is never treated as healthy. Worker health is PostgreSQL heartbeats, not Docker
`/tmp` readiness files. Database health is an API engine ping when `THYTRADER_DATABASE_URL` is set.

## Workflow

1. Verify CLI help and run `health` first.
2. If stderr says the API version or ops contract does not match the CLI, rebuild with `make run` (ask first). Package version `0.1.0` is not enough.
3. If degraded or failed, follow `recommended_next_action` and inspect `components[].reason_code`.
4. Gather only the extra report needed (market-data, strategies, runtime, performance, reconciliation).
5. Keep `mode` (`backtest` / `paper` / `live`), timeframe (`1h` or `5m`), strategy fingerprint, and dataset fingerprint in any answer. Performance timeframe is the published strategy's clock for backtest, paper, and live. Paper/live `total_net_pnl` is a fill ledger (realized/unrealized, fees, drawdown) marked at last close; `MISSING_MARK` means open inventory was not marked.
6. Treat `partial_result_warnings` as incomplete evidence, not as health.
7. Separate verified report fields from hypotheses.
8. Stop. Watchlist/ingest/gap-fill require `skills/thytrader-data/SKILL.md` and `--confirm`. Draft/publish/backtest require `skills/thytrader-research/SKILL.md` and `--confirm`. Deploy, pause, resume, stop, and live arming require `skills/thytrader-runtime/SKILL.md` with `--confirm` (live also `--i-understand-live`).

## Forbidden

- Printing API keys, private keys, `.env` values, or database URLs
- `GET /api/v1/market-data/preview` as the operator contract (dashboard-only)
- Browser clicking as a substitute for these endpoints
- Paper or live order control
- Silently using `--local` because HTTP failed
- Editing application source to "fix" a running instance

See [diagnostics-api.md](references/diagnostics-api.md) and [report-schemas.md](references/report-schemas.md).
