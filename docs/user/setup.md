# Setup

The supported local stack uses Docker Compose. It starts PostgreSQL, applies the explicit Alembic
migration, then starts the API, portfolio worker, market-data worker, execution worker, and web UI
with health checks and **loopback-only** host ports.

Needs Docker Compose and [`uv`](https://docs.astral.sh/uv). From a fresh clone:

```bash
make run
```

`make run` wraps `scripts/setup_local_stack.py`. The equivalent direct invocation is:

```bash
uv run python scripts/setup_local_stack.py
```

The command is safe to rerun. It preserves unrelated ignored `.env` entries, creates matching
local-only database settings when needed, refuses to replace a user-managed database URL, builds the
images, starts and waits for a healthy PostgreSQL service, runs Alembic as a one-shot gate, then
starts API, the workers, and web only after migration succeeds. The final startup waits for all
service health checks and **does not print credentials or connection URLs**. It also recognizes and
safely updates ThyTrader's former generated `127.0.0.1:5433` database URL while continuing to reject
arbitrary custom URLs. Duplicate ThyTrader-managed database keys are rejected as ambiguous rather
than partially rewritten.

| What | Where |
|---|---|
| Dashboard | http://127.0.0.1:5175 |
| API ready | http://127.0.0.1:8200/health/ready |
| PostgreSQL | `127.0.0.1:5439` (loopback only) |

`THYTRADER_API_PORT` defaults to `8200`. If you override it in ignored `.env`, Compose applies the
same value to the API listener, loopback host mapping, API readiness probe, and the web container's
internal proxy target.

## Credentials (names only)

Leave Coinbase variables empty for deterministic demo balances. Configure both
`THYTRADER_COINBASE_API_KEY_NAME` and `THYTRADER_COINBASE_API_PRIVATE_KEY` in ignored `.env`, or
set/rotate/clear them from loopback Settings (`http://127.0.0.1:5175/settings`) or
`thytrader-runtime set-coinbase-credentials --private-key-file … --confirm`. GET never echoes
secrets. Workers still interpolate host `.env` at start and need a restart. Setting credentials
does not arm live trading. ThyTrader accepts View + Trade keys and keys with additional
permissions. The portfolio screen is read-only; it never submits an order.

Never commit `.env`. `.env.example` lists names and placeholders only. Startup must not print
secrets. See [Safety](safety.md).

## Day-to-day Compose

```bash
make status   # service health
make logs     # follow API, workers, and web logs
make down     # tear down Compose services (preserves database and market-data volumes)
make stop     # synonym for make down
```

Inspect or stop without Make:

```bash
docker compose ps
docker compose logs -f api worker market-data-worker execution-worker web
docker compose down
```

A normal `docker compose down` (and `make down` / `make stop`) preserves PostgreSQL and immutable
market-data volumes. Only `docker compose down -v` destroys them and is intentionally destructive.

## Portfolio snapshots and the dashboard

The portfolio worker takes a snapshot at startup and then every five minutes by default. Configure a
value between 60 seconds and 24 hours in `thytrader.yaml` (`snapshot_interval_seconds`). The change
applies without restart. The dashboard Refresh button is read-only; it never creates history points.

The portfolio-history panel offers `24H`, `7D`, `30D`, and `All` ranges. The API performs the range
query and bounds the response to representative observations, preserving the range endpoints without
returning an unbounded browser payload. The panel compares the latest value with the oldest
displayed observation, exposes exact point timestamps and values to pointer and keyboard users, and
marks snapshot cadence as behind when the latest persisted observation is more than two configured
sampling intervals old. Gaps remain visible rather than being interpolated.

The dashboard includes **Data-source diagnostics**: a read-only connection and candle-integrity
check for a selected USD spot product. It shows request-time validation plus the separate worker's
durable coverage and failure state; it is **not** a price chart, trading signal, profitability
result, or trading-readiness claim. It uses Coinbase data when credentials are configured, or
deterministic demo data otherwise.

## Market-data worker

The separately supervised market-data worker maintains complete-only verified Parquet datasets for
1h, 5m, 15m, 30m, 6h, 1d, 1m, 2h, and 4h (each is also a legal strategy, paper, live, and HTF
clock), publishes only complete verified Parquet and manifests, and retries every five minutes by
default. PostgreSQL records its latest attempt, verified coverage, freshness, fingerprint, and
redacted failure state. Cadence, lookback, and default product live in `thytrader.yaml` and apply
without restart. Dataset root stays env-at-boot (`THYTRADER_MARKET_DATA_DATASET_ROOT`). Compose mounts
that immutable dataset volume read-write only in the market-data worker and read-only in the API so
browser dataset selection and backtest verification consume the exact artifacts the worker published.

Inspect ingestion evidence or restart only that failure domain:

```bash
curl -sS 'http://127.0.0.1:8200/api/v1/market-data/ingestion?product_id=BTC-USD'
docker compose restart market-data-worker
docker compose logs --tail=100 market-data-worker
```

Restart and automatic retries are idempotent for an unchanged aligned range. A failed attempt remains
visible until a later verified publication succeeds; neither the endpoint nor dashboard refresh
starts ingestion.

When the aligned lookback window advances, the worker publishes a new immutable dataset. Overlapping
hourly windows therefore accumulate by design and are not automatically pruned. Size the
`thytrader_market_data` volume accordingly; do not manually remove manifests or Parquet files while
ThyTrader is running. Automated retention is deferred until catalog/reference tracking can prove that
no reproducible consumer still names a fingerprint.

## Native processes (iteration)

Use native processes for fast backend or frontend iteration. ThyTrader requires Python 3.14 and
`uv`; install the locked environment with `uv sync`. The web workspace is pinned to Node `22.23.1`
in `.nvmrc`, so run `nvm use` before its first install.

```bash
uv sync
uv run thytrader-api
uv run thytrader-worker
uv run thytrader-market-data-worker
uv run thytrader-execution-worker
cd web && npm ci && npm run dev -- --open
```

For a user-managed PostgreSQL instance, set `THYTRADER_DATABASE_URL` in ignored `.env`, apply the
explicit migration, then start the API and workers as separate processes:

```bash
uv run alembic upgrade head
uv run thytrader-api
uv run thytrader-worker
uv run thytrader-market-data-worker
uv run thytrader-execution-worker
```

Contributor quality gates live in [contributor documentation](../contributing.md).
