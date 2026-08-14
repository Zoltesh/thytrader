# ThyTrader

Thine trading platform. A customizable platform you control: strategies, risk, analysis, markets, and portfolio management—configured by you or with the help of agents you choose.

ThyTrader is being designed as an open-source, local-first trading workstation with a FastAPI backend, SvelteKit frontend, reproducible backtesting, and guarded automated execution.

Start with the [project documentation](docs/README.md) for the product direction, architecture, safety baseline, delivery roadmap, and accepted decisions. Contributors and coding agents should also read [`AGENTS.md`](AGENTS.md).

## Clone-and-run local stack

The supported local stack uses Docker Compose. It starts PostgreSQL, applies the explicit Alembic
migration, then starts the API, portfolio worker, market-data worker, and web UI—with health checks and
loopback-only host ports. From a fresh clone with Docker Compose and [`uv`](https://docs.astral.sh/uv)
installed, run:

```bash
uv run python scripts/setup_local_stack.py
```

The command is safe to rerun. It preserves unrelated ignored `.env` entries, creates matching
local-only database settings when needed, refuses to replace a user-managed database URL, builds the
images, starts and waits for a healthy PostgreSQL service, runs Alembic as a one-shot gate, then
starts API, both workers, and web only after migration succeeds. The final startup waits for all
service health checks and does not print credentials or connection URLs. It also recognizes and
safely updates ThyTrader's former generated `127.0.0.1:5433` database URL while continuing to reject
arbitrary custom URLs. Duplicate ThyTrader-managed database keys are rejected as ambiguous rather
than partially rewritten.

- Dashboard: `http://127.0.0.1:5175`
- API readiness: `http://127.0.0.1:8200/health/ready`
- PostgreSQL: `127.0.0.1:5439` (loopback only)

`THYTRADER_API_PORT` defaults to `8200`. If you override it in ignored `.env`, Compose applies the
same value to the API listener, loopback host mapping, API readiness probe, and the web container's
internal proxy target.

The worker takes a snapshot at startup and then every five minutes by default. Configure a value
between 60 seconds and 24 hours with `THYTRADER_SNAPSHOT_INTERVAL_SECONDS` in ignored `.env`.
The dashboard Refresh button is read-only; it never creates history points.

The separately supervised market-data worker retrieves the latest aligned seven-day 1h range,
publishes only complete verified Parquet and manifests, and retries every five minutes by default.
PostgreSQL records its latest attempt, verified coverage, freshness, fingerprint, and redacted failure
state. Its cadence, lookback, target, and dataset root are configurable through the documented
`THYTRADER_MARKET_DATA_*` variables in ignored `.env`. Compose mounts that immutable dataset volume
read-write only in the market-data worker and read-only in the API so browser dataset selection and
backtest verification consume the exact artifacts the worker published.

The portfolio-history panel offers `24H`, `7D`, `30D`, and `All` ranges. The API performs the
range query and bounds the response to representative observations, preserving the range endpoints
without returning an unbounded browser payload. The panel compares the latest value with the oldest
displayed observation, exposes exact point timestamps and values to pointer and keyboard users, and
marks snapshot cadence as behind when the latest persisted observation is more than two configured
sampling intervals old. Gaps remain visible rather than being interpolated.

The dashboard includes **Data-source diagnostics**: a read-only connection and candle-integrity
check for a selected USD spot product. It shows request-time validation plus the separate worker's
durable coverage and failure state; it is **not** a price chart, trading signal, profitability result,
or trading-readiness claim. It uses Coinbase data when credentials are configured,
or deterministic demo data otherwise. See the [market-data pipeline](docs/architecture/market-data.md)
for the implemented operational contract and remaining increments.

Inspect or stop the stack with:

```bash
docker compose ps
docker compose logs -f api worker market-data-worker web
docker compose down
```

Inspect ingestion evidence or restart only its independent failure domain with:

```bash
curl -sS 'http://127.0.0.1:8200/api/v1/market-data/ingestion?product_id=BTC-USD'
docker compose restart market-data-worker
docker compose logs --tail=100 market-data-worker
```

Restart and automatic retries are idempotent for an unchanged aligned range. A failed attempt remains
visible until a later verified publication succeeds; neither the endpoint nor dashboard refresh starts
ingestion.

When the aligned lookback window advances, the worker publishes a new immutable dataset. Overlapping
hourly windows therefore accumulate by design and are not automatically pruned. Size the
`thytrader_market_data` volume accordingly; do not manually remove manifests or Parquet files while
ThyTrader is running. Automated retention is deferred until catalog/reference tracking can prove that
no reproducible consumer still names a fingerprint.

A normal `docker compose down` preserves PostgreSQL and immutable market-data volumes. Only
`docker compose down -v` destroys them and is intentionally destructive.

## Native development

Use native processes for fast backend or frontend iteration. ThyTrader requires Python 3.14 and
`uv`; install the locked environment with `uv sync`. The web workspace is pinned to Node `22.23.1`
in `.nvmrc`, so run `nvm use` before its first install.

```bash
uv sync
uv run thytrader-api
uv run thytrader-worker
uv run thytrader-market-data-worker
cd web && npm ci && npm run dev -- --open
```

The first usable vertical slice displays deterministic demo balances when Coinbase credentials are
empty and live balances when both Coinbase variables are configured. ThyTrader accepts View + Trade
keys and keys with additional permissions; this read-only screen never submits an order.

For a user-managed PostgreSQL instance, set `THYTRADER_DATABASE_URL` in ignored `.env`, apply the
explicit migration, then start the API and both workers as separate processes:

```bash
uv run alembic upgrade head
uv run thytrader-api
uv run thytrader-worker
uv run thytrader-market-data-worker
```
Run frontend verification with:

```bash
cd web
npx playwright install chromium  # first run on a new machine only
npm run lint
npm run check
npm run test
npm run build
```

Run the canonical backend quality gates with:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

## Read-only research signal evaluation

An existing published research run that explicitly selects `thytrader-bar-signal-v1` can be replayed
against its exact verified strategy and Parquet dataset:

```bash
uv run thytrader-research-evaluate <run_fingerprint> --pretty
```

The command prints a deterministic completed-candle entry-condition trace and its SHA-256 fingerprint.
It does not publish a run, create an order intent, apply cooldown, simulate entries or exits, calculate
PnL, persist results, or mutate trading state. The browser research workflow separately creates a
constrained reference draft, publishes an immutable strategy, selects a verified dataset, submits a
deterministic backtest, and opens its immutable result detail; it has no paper or live trading authority.
See the [signal-evaluation contract](docs/architecture/signal-evaluation.md) and
[backtest simulation](docs/architecture/backtest-simulation.md) for exact semantics and limits.
