# ThyTrader

Thine trading platform. A customizable platform you control: strategies, risk, analysis, markets, and portfolio management—configured by you or with the help of agents you choose.

ThyTrader is being designed as an open-source, local-first trading workstation with a FastAPI backend, SvelteKit frontend, reproducible backtesting, and guarded automated execution.

Start with the [project documentation](docs/README.md) for the product direction, architecture, safety baseline, delivery roadmap, and accepted decisions. Contributors and coding agents should also read [`AGENTS.md`](AGENTS.md).

## Backend development

ThyTrader requires Python 3.14 and [`uv`](https://docs.astral.sh/uv/). Install the locked
environment with:

```bash
uv sync
```

Copy `.env.example` to `.env` only when local overrides are needed. Secret placeholders are
empty by design, `.env` is ignored by Git, and the API binds to `127.0.0.1` by default.

Run the API and persistent worker in separate terminals:

```bash
uv run thytrader-api
uv run thytrader-worker
```

The API exposes liveness at `http://127.0.0.1:8200/health/live`, startup readiness at
`http://127.0.0.1:8200/health/ready`, and the portfolio at
`http://127.0.0.1:8200/api/v1/portfolio`. If you copied an earlier `.env.example`, update
`THYTRADER_API_PORT` to `8200` before starting the API.

## Portfolio UI

The first usable vertical slice displays deterministic demo balances when Coinbase credentials are
empty and live balances when both Coinbase variables are configured. ThyTrader accepts View + Trade
keys and keys with additional permissions; this read-only screen never submits an order.

In a second terminal, install and start the SvelteKit application:

```bash
cd web
npm ci
npm run dev -- --open
```

The UI opens at `http://127.0.0.1:5175` and proxies `/api` requests to the local FastAPI process.
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
