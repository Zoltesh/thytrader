# ops/AGENTS.md

This workspace operates a **running** ThyTrader instance. It is not the contributor checkout.

Use only the shipped skills:

- `thytrader-operator` — read-only diagnostics
- `thytrader-data` — watchlist, ingest, gap-fill (`--confirm`)
- `thytrader-research` — drafts, publish, backtests (`--confirm`)
- `thytrader-runtime` — paper/live start/pause/resume/stop (`--confirm`; live also `--i-understand-live`)

Run every `uv run thytrader-*` command from the **repository root** (the parent of this `ops/`
folder). JSON is the default CLI output.

Backtest engines `thytrader-bar-backtest-v1` / `v2` / `v3` are parallel research contracts (not
"newer app versions"). Pick per `skills/thytrader-research/SKILL.md` (when-to-pick table). Do not
confuse them with Coinbase Advanced Trade REST v3.

## Hard stop

Do not edit `src/`, `compose.yaml`, Dockerfiles, Alembic, tests, or this repository's Python.
Do not search the tree for a code fix. Do not make the API dataset volume writable.
Do not interpolate missing candles. Do not print `.env` or secrets.

Report skill and CLI failures. Rebuild or restart only with `make run` from the repository root
when the user asked, or when health/HTTP says the Compose image is stale (version mismatch,
ops-contract mismatch, or 404 on agent routes while `/health/ready` is 200).

There is no GitNexus contributor workflow in this workspace.
