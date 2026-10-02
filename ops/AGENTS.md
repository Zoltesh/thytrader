# ops/AGENTS.md

This workspace operates a **running** ThyTrader instance. It is not the contributor checkout.

Use only the shipped skills:

- `thytrader-operator` — read-only diagnostics
- `thytrader-data` — watchlist, ingest, gap-fill (`--confirm`)
- `thytrader-research` — strategy create/save/import/clone/delete, backtests, studies (`--confirm`)
- `thytrader-runtime` — paper/live start/pause/resume/stop (single bots and `portfolio-*` for whole portfolios), on-demand `place-order`, and risk-policy publication (`--confirm`; live start, live resume, and live place-order also `--i-understand-live`)
- `thytrader-portfolio` — portfolios, sleeves, weights, limits, manager settings, portfolio backtests, journal, and the manager loop (briefing, proposals, a person's approve/decline) (`--confirm`; no deployment or order authority)
- `thytrader-playbook` — data → research → optional paper via existing CLIs (`--confirm` forwarded; never live)
- `thytrader-memory` — journals, sentiment/pattern hooks, monitor, notify, train/list-models/show-model (`--confirm`; YOLO never covers this lane)

Run every `uv run thytrader-*` command from the **repository root** (the parent of this `ops/`
folder). JSON is the default CLI output.

Backtests use one model, `engine: "thytrader-backtest"`; there is no engine to pick. Read its
fill, fee, slippage, and spread-stress assumptions with `uv run thytrader-research backtest-model`
and follow `skills/thytrader-research/SKILL.md`. Requests name no engine version (sending
one is rejected with HTTP 422); `spread_bps` is an optional disclosed spread stress.

## Hard stop

Do not edit `src/`, `compose.yaml`, Dockerfiles, Alembic, tests, or this repository's Python.
Do not search the tree for a code fix. Do not make the API dataset volume writable.
Do not interpolate missing candles. Do not print `.env` or secrets.

Report skill and CLI failures. Rebuild or restart only with `make run` from the repository root
when the user asked, or when health/HTTP says the Compose image is stale (version mismatch,
ops-contract mismatch, or 404 on agent routes while `/health/ready` is 200).

There is no GitNexus contributor workflow in this workspace.
