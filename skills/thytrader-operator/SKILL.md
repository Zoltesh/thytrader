---
name: thytrader-operator
description: >-
  Diagnose a running ThyTrader instance through the versioned read-only operator
  CLI and HTTP API. Use when checking health, configuration, Coinbase connectivity,
  market-data freshness, strategy/runtime status, backtest or paper/live performance,
  reconciliation, or a redacted support bundle. Never places, edits, or cancels
  orders and never arms live trading. `chat-status` reports whether an in-app LLM
  key is held in the API process; it never prints the key and is not Coinbase.
---

# ThyTrader operator

Read-only diagnostics for a running instance. Do not scrape logs, query PostgreSQL, or import private internals.

Schema version: `thytrader-operator-report-v1` (`schema_version` on every JSON report).

Default transport is the loopback HTTP API (`THYTRADER_API_BASE_URL` or `http://127.0.0.1:8200`). Pass `--local` only when you intentionally want process stores instead of HTTP. Do not fall back from HTTP to PostgreSQL if the API is down.

Production installs advertise trust-boundary status at `GET /api/v1/security/status` (no secrets).
Read-only operator routes stay unauthenticated; mutations use installation auth per
[ADR 0061](../../docs/decisions/0061-application-trust-boundary.md).

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
| Market data | `uv run thytrader-operator market-data [--product-id BTC-USD] [--timeframe 1h\|5m\|15m\|30m\|6h\|1d\|1m\|2h\|4h]` | `GET /api/v1/operator/market-data` |
| Data catalog | `uv run thytrader-operator data-catalog` | `GET /api/v1/operator/data-catalog` |
| Products | `uv run thytrader-operator products` | `GET /api/v1/operator/products` |
| Indicators | `uv run thytrader-operator indicators` | `GET /api/v1/operator/indicators` |
| Strategies / runtimes | `uv run thytrader-operator strategies` | `GET /api/v1/operator/strategies` |
| Runtime watch | `uv run thytrader-operator runtime [--deployment-id UUID]` | `GET /api/v1/operator/runtime` |
| Monitor | `uv run thytrader-operator monitor` | `GET /api/v1/operator/monitor` (deployments, recent journals, notify delivery; omits balances and webhook URLs) |
| Why-trade review | `uv run thytrader-operator trade-reasons [--intent-id UUID] [--deployment-id UUID]` | `GET /api/v1/operator/trade-reasons` |
| Performance | `uv run thytrader-operator performance --result-fingerprint sha256:…` or `--deployment-id UUID` | `GET /api/v1/operator/performance` |
| Risk | `uv run thytrader-operator risk` | `GET /api/v1/operator/risk` (registry identity, slot counts, breaker fractions/ints, pause/mismatch; omits balances) |
| Reconciliation | `uv run thytrader-operator reconciliation` | `GET /api/v1/operator/reconciliation` |
| Studies | `uv run thytrader-operator studies` | `GET /api/v1/operator/studies` (persisted research-study catalog rows; omits child equity) |
| Portfolio | `uv run thytrader-operator portfolio` | `GET /api/v1/operator/portfolio` (balances with `balances_omitted=false`; never credentials) |
| Fees | `uv run thytrader-operator fees` | `GET /api/v1/operator/fees` (fee tier plus research-only suggested maker/taker) |
| Support bundle | `uv run thytrader-operator support-bundle` | `GET /api/v1/operator/support-bundle` |
| Schema check | `uv run thytrader-operator schema-check` | (local files only) |
| In-app LLM key flag | `uv run thytrader-operator chat-status` | `GET /api/v1/operator-chat/status` (HTTP-only; never prints the key; not Coinbase; `--local` is rejected) |

`--format text` is a short summary. Parent flags such as `--format` may follow the subcommand.

Machine-readable envelope: [operator-report-v1.schema.json](references/operator-report-v1.schema.json).

`strategies` and `runtime` deployment rows include redacted `books[]` (`product_id`, `phase`,
`side`, `protection_status`) without quantities ([ADR 0060](../../docs/decisions/0060-multi-book-deployment-api.md)).
`protection_status` is `flat` / `covered` / `unprotected` / `unknown` from verified attached-child
coverage and venue-visible exits, not inferred parent geometry
([ADR 0058](../../docs/decisions/0058-protection-lifecycle-accounting.md)). Rows also include
`lifecycle_command`, breaker latches (`daily_loss_latched`, `drawdown_latched`), optimistic
`revision`, and `worker_lease_held` (boolean only; no holder identity). Latches persist across
pause and managed shutdown until an explicit operator reset via
`thytrader-runtime reset-breaker-latches UUID --confirm` /
`POST /api/v1/deployments/{id}/reset-breaker-latches` (always `--confirm`; YOLO never skips).
Pause still maintains protection;
it only blocks new entries and risk-up reprice. Default stop is managed shutdown; flatten is
explicit (`--flatten` / `?flatten=true`). Live capital (`allocated_capital`,
`venue_available_quote`) is on `thytrader-runtime show` / `GET /api/v1/deployments/{id}` — this
skill omits cash. A secondary open book is never implied by the deployment primary `product_id`.
For sizes, orders, and fills use `thytrader-runtime show` (`positions`, `instrument_runtimes`,
product-tagged orders/fills, `book_totals`). The singular HTTP `position` field is
compatibility-only.

## Portfolio vs deployment inventory

Three read-only surfaces answer different questions. Do not conflate them.

| Question | Surface | Access |
| --- | --- | --- |
| Account balances and portfolio history (demo or Coinbase) | Account portfolio API | `GET /api/v1/portfolio`, `GET /api/v1/portfolio/history?range=7d\|24h\|30d\|forever` — **no** `thytrader-operator` subcommand today |
| Deployment quantities, orders, fills, capital, protection | Runtime inventory | `uv run thytrader-runtime show DEPLOYMENT_ID` / `GET /api/v1/deployments/{id}?detail=full` (default `detail=summary` omits historical orders/fills; paginate `.../fills` and `.../orders`) |
| Diagnostic phase/side/protection without sizes | Operator reports | `strategies`, `runtime` (`books[]` redacted) |

`health` may list a `portfolio_history` component (snapshot freshness). That is not holdings.
`risk` and `monitor` omit balances (`balances_omitted: true`). For a numbered portfolio → research
recipe using these surfaces, see
[`docs/agent/portfolio-research-ops-playbook.md`](../../docs/agent/portfolio-research-ops-playbook.md).

## Exit codes

- `0` overall `healthy` (schema-check success is also `0`)
- `1` overall `degraded`
- `2` overall `failed` (schema-check mismatch is also `2`)
- argparse usage errors use the interpreter's usual non-zero code

Missing telemetry is never treated as healthy. Worker health is PostgreSQL heartbeats, not Docker
`/tmp` readiness files. The market-data worker is stale after two ingest intervals plus slack, not
the 5-second ingest-request poll; it heartbeats between ingest cells and UTC-day chunks
([ADR 0072](../../docs/decisions/0072-catalog-health-bounded-gaps-self-complete-ingest.md)).
Database health is an API engine ping when `THYTRADER_DATABASE_URL` is set.

## Workflow

1. Verify CLI help and run `health` first. Expect ops contract `thytrader-ops-contract-v36`,
   Alembic revision `0046`, `spot_quote_currencies` `USD`/`USDC`/`USDT`, `catalog_health`, bounded
   deployment reads (`list`, `summary`, `fills`, `orders`), cursor ledger pagination, and
   multi-book ledger on a current image ([ADR 0074](../../docs/decisions/0074-multi-book-ledger-bounded-reads.md),
   [ADR 0064](../../docs/decisions/0064-deployment-http-lifecycle-and-breaker-latch-reset.md),
   [ADR 0065](../../docs/decisions/0065-deployment-capital-accounting-http.md),
   [ADR 0066](../../docs/decisions/0066-research-ops-contract-v4.md),
   [ADR 0068](../../docs/decisions/0068-slow-timeframe-watch-lookback-and-catalog-ingest.md),
   [ADR 0069](../../docs/decisions/0069-async-backtest-jobs-study-summary.md),
   [ADR 0071](../../docs/decisions/0071-usdc-spot-quote-markets.md),
   [ADR 0072](../../docs/decisions/0072-catalog-health-bounded-gaps-self-complete-ingest.md),
   [ADR 0073](../../docs/decisions/0073-durable-research-jobs.md)). Mismatch means rebuild with
   `make run`.
2. If the CLI exits because the API version or ops contract does not match this checkout, rebuild with `make run` (ask first). Package version `0.1.0` is not enough. Do not treat a printed report plus a warning as success.
3. If degraded or failed, follow `recommended_next_action` and inspect `components[].reason_code`.
4. Gather only the extra report needed (market-data, strategies, runtime, performance, reconciliation, studies).
   In `data-catalog`, judge configured coverage by `watch_complete`; `complete` describes only the
   current contiguous island. Each row also carries a `watch_status` noun
   (`complete` / `backfilling` / `unknown`) so `worker_status=succeeded` — which describes the
   latest chunk only — cannot be misread as a finished backfill. `sparsity` is island-only; use
   `watch_sparsity` for the configured
   lookback. Failed rows expose redacted `failure_code` / `failure_message` ([ADR 0068](../../../docs/decisions/0068-slow-timeframe-watch-lookback-and-catalog-ingest.md)).
   If `watch_complete` is false, use `thytrader-data inspect-gaps` for
   classified holes. If that report sets `truncated`, the `gap_summary` is partial (time/row budget)
   and is not proof the full watch was scanned ([ADR 0072](../../docs/decisions/0072-catalog-health-bounded-gaps-self-complete-ingest.md)).
   Cover HTF-filter and per-indicator extra clocks the same way. Never interpolate.
5. Keep `mode` (`backtest` / `paper` / `live`), timeframe (any ingested venue clock: `1m`, `5m`,
   `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, or `1d`), strategy fingerprint, and dataset fingerprint in
   any answer. Performance timeframe is the published strategy's clock, or the discretionary book
   clock. Paper/live `total_net_pnl` is a fill ledger (realized/unrealized, fees, drawdown) marked
   at last close; `MISSING_MARK` means open inventory was not marked. Live REST fill ingest uses
   documented List Fills **cursor** pagination (not `has_next`) and quarantines incomplete or
   unparseable rows ([ADR 0059](../../docs/decisions/0059-coinbase-list-fills-cursor-pagination.md));
   do not treat a truncated or failed fill page as a complete ledger.
6. Treat `partial_result_warnings` as incomplete evidence, not as health.
7. Separate verified report fields from hypotheses.
8. Stop. Watchlist/ingest/gap-fill require `skills/thytrader-data/SKILL.md` and `--confirm`. Draft/publish/backtest require `skills/thytrader-research/SKILL.md` and `--confirm`. Deploy, pause, resume, stop, live arming, risk-policy publication, and Coinbase credential show/set/clear require `skills/thytrader-runtime/SKILL.md` with `--confirm` unless YOLO covers that tier (live start also `--i-understand-live`). Credential set/clear always need `--confirm`; YOLO never covers them. Sequencing data → research → optional paper uses `skills/thytrader-playbook/SKILL.md` and still never starts live. Journals, sentiment/pattern hooks, notify, and fail-closed `train` use `skills/thytrader-memory/SKILL.md` with `--confirm`; YOLO never covers that lane.

## Forbidden

- Printing API keys, private keys, `.env` values, or database URLs
- `GET /api/v1/market-data/preview` as the operator contract (dashboard-only)
- Browser clicking as a substitute for these endpoints
- Paper or live order control
- Silently using `--local` because HTTP failed
- Editing application source to "fix" a running instance

See [diagnostics-api.md](references/diagnostics-api.md) and [report-schemas.md](references/report-schemas.md).

YOLO on/off and independent tiers (`data`, `research`, `paper`, `live`) live in `thytrader.yaml`
([ADR 0055](../../docs/decisions/0055-yaml-settings-runtime-reloadable-yolo.md)). They apply without
restart. Leftover `THYTRADER_YOLO_TIERS=paper` is valid; do not JSON-encode the env list.
`GET /api/v1/operator/configuration` reports `yaml_source_of_truth`, `settings_file`,
`yaml_loaded`, and `effective_api_base_url` (the loopback origin agent CLIs resolve for this
checkout — use it instead of probing guessed ports). Mutations use `thytrader-runtime
show-settings` / `set-settings --confirm` or `GET`/`PUT /api/v1/settings`. This skill stays
read-only.

## In-app operator chat

Loopback UI: `/chat`. HTTP: `/api/v1/operator-chat`
([ADR 0051](../../docs/decisions/0051-in-app-operator-chat.md)). The user pastes **their** LLM API
key into the API process (`PUT /api/v1/operator-chat/credentials`). That is **not** the Coinbase
secrets surface (`/settings` and `thytrader-runtime` show/set/clear-coinbase-credentials). Status
never returns `api_key`. Coinbase keys never go to the browser.

The chat invokes the same versioned HTTP skill routes as these CLIs. Operator tools stay read-only.
Data, research, runtime, and memory mutations wait on in-app confirmation (`--confirm`). Live start
and live place-order also need the understand-live checkbox. YOLO never skips understand-live.
Memory always confirms. The playbook never starts live. This skill stays read-only; chat is not
extra trading authority and not a substitute for the lane skills.

`chat-status` reports `llm_configured` only (`thytrader-operator-chat-v1`, not
`thytrader-operator-report-v1`).
