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

The API exposes liveness at `http://127.0.0.1:8000/health/live` and startup readiness at
`http://127.0.0.1:8000/health/ready`.

Run the canonical backend quality gates with:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
```
