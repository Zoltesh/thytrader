# ThyTrader

Thine trading platform. A customizable platform you control: strategies, risk, analysis, markets, and portfolio management—configured by you or with the help of agents you choose.

ThyTrader is being designed as an open-source, local-first trading workstation with a FastAPI backend, SvelteKit frontend, reproducible backtesting, and guarded automated execution.

Start with the [project documentation](docs/README.md) for the product direction, architecture, safety baseline, delivery roadmap, and accepted decisions. Contributors and coding agents should also read [`AGENTS.md`](AGENTS.md).

## Clone-and-run local stack

The supported local stack uses Docker Compose. It starts PostgreSQL, applies the explicit Alembic
migration, then starts the API, scheduled snapshot worker, and web UI—with health checks and
loopback-only host ports. From a fresh clone with Docker Compose and [`uv`](https://docs.astral.sh/uv)
installed, run:

```bash
uv run python scripts/setup_local_stack.py
```

The command is safe to rerun. It preserves unrelated ignored `.env` entries, creates matching
local-only database settings when needed, refuses to replace a user-managed database URL, builds the
images, waits for services to become healthy, and does not print credentials or connection URLs.

- Dashboard: `http://127.0.0.1:5175`
- API readiness: `http://127.0.0.1:8200/health/ready`
- PostgreSQL: `127.0.0.1:5433` (loopback only)

The worker takes a snapshot at startup and then every five minutes by default. Configure a value
between 60 seconds and 24 hours with `THYTRADER_SNAPSHOT_INTERVAL_SECONDS` in ignored `.env`.
The dashboard Refresh button is read-only; it never creates history points.

The portfolio-history panel offers `24H`, `7D`, `30D`, and `All` ranges. The API performs the
range query and bounds the response to representative observations, preserving the range endpoints
without returning an unbounded browser payload. The panel compares the latest value with the oldest
displayed observation, exposes exact point timestamps and values to pointer and keyboard users, and
marks snapshot cadence as behind when the latest persisted observation is more than two configured
sampling intervals old. Gaps remain visible rather than being interpolated.

The dashboard also contains a read-only USD-spot market-data preview with a product selector. It
validates closed candles, venue constraints, gaps, and freshness using Coinbase data (when
credentials are configured) or deterministic BTC-USD, ETH-USD, and SOL-USD demo data. It is not
persisted historical ingestion and no strategy or trading path uses it yet; see the
[market-data pipeline](docs/architecture/market-data.md) for scope.

Inspect or stop the stack with:

```bash
docker compose ps
docker compose logs -f api worker web
docker compose down
```

A normal `docker compose down` preserves the local PostgreSQL volume. Only
`docker compose down -v` destroys portfolio history and is intentionally destructive.

## Native development

Use native processes for fast backend or frontend iteration. ThyTrader requires Python 3.14 and
`uv`; install the locked environment with `uv sync`. The web workspace is pinned to Node `22.23.1`
in `.nvmrc`, so run `nvm use` before its first install.

```bash
uv sync
uv run thytrader-api
uv run thytrader-worker
cd web && npm ci && npm run dev -- --open
```

The first usable vertical slice displays deterministic demo balances when Coinbase credentials are
empty and live balances when both Coinbase variables are configured. ThyTrader accepts View + Trade
keys and keys with additional permissions; this read-only screen never submits an order.

For a user-managed PostgreSQL instance, set `THYTRADER_DATABASE_URL` in ignored `.env`, apply the
explicit migration, then start the API and worker as separate processes:

```bash
uv run alembic upgrade head
uv run thytrader-api
uv run thytrader-worker
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
