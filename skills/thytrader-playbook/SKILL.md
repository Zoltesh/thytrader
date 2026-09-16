---
name: thytrader-playbook
description: >-
  Sequence ThyTrader data health, research, and optional paper through existing
  lane CLIs via thytrader-playbook. Use when the user asks to run the agent
  playbook or to go from watchlist/ingest through draft, publish, backtest, and
  optional paper in one workflow. Mutations still require --confirm unless YOLO
  covers that tier. Never starts live trading and never passes --i-understand-live.
  Not an extension of operator, data, research, or runtime skills.
---

# ThyTrader playbook

Orchestration over **existing** CLIs. This skill is not an extension of `thytrader-operator`,
`thytrader-data`, `thytrader-research`, or `thytrader-runtime`. It does not grant live authority.

HTTP-only against the loopback API (`THYTRADER_API_BASE_URL` or `http://127.0.0.1:8200`). There is
no `--local` database mode. The playbook calls `thytrader-operator`, `thytrader-data`,
`thytrader-research`, and `thytrader-runtime` `main()` functions. It never constructs
`--mode live` or `--i-understand-live`.

Default remains `--confirm` on every child mutation. YOLO (operator-enabled, default off) may skip
`--confirm` on advertised tiers `data`, `research`, `paper`, and/or `live`. This playbook still
never starts live, never passes `--i-understand-live`, and never uses a `live` YOLO tier.
`set-risk-policy` is not part of this playbook.

## Hard stop

When operating a running instance, do not edit `src/`, `compose.yaml`, Dockerfiles, Alembic, or tests.
Do not search the tree for a code patch. Report failures through this skill. Every command preflights
the full `/health/ready` ops contract. Rebuild or restart only with `make run` when the user asked,
or when the CLI reports a version or ops-contract mismatch, or HTTP 404 on an agent route while
`/health/ready` is 200 (the shared stale-image signal). Matching `0.1.0` alone is not current-image
evidence. Open the `ops/` workspace instead of the git root. Run every
`uv run thytrader-*` command from the repository root (the parent of `ops/`).

## Commands

| Need | Command |
|---|---|
| Show Safe vs YOLO | `uv run thytrader-playbook status` |
| Health + watchlist only | `uv run thytrader-playbook run --product-id ETH-USD --timeframe 5m` |
| Ensure watch + ingest | `uv run thytrader-playbook run --product-id ETH-USD --timeframe 1m --ingest --confirm` |
| Create a draft | `uv run thytrader-playbook run --create-draft --confirm` |
| Publish + backtest | `uv run thytrader-playbook run --publish --strategy-id UUID --backtest-file request.json --confirm` |
| Optional paper | `uv run thytrader-playbook run --paper-cash 10000 --strategy-fingerprint sha256:… --confirm` |

`status` is read-only. `run` forwards `--confirm` to child mutations (`watch-add`, `ingest`,
`create-draft`, `publish`, `submit-backtest`, paper `start`). It never starts live.
`--timeframe` may be any ingested venue clock (`1m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`,
`6h`, `1d`; default `1h`). This playbook watches only that decision clock. Extra HTF or
per-indicator clocks still need `thytrader-data` ingest.

Underlying HTTP used by this CLI:

- `GET /health/ready` (ops-contract preflight)
- `GET /api/v1/agent-orchestration`
- Child CLIs keep their own routes (`/api/v1/data`, `/api/v1/strategies`, `/api/v1/backtests`,
  `/api/v1/deployments`). YOLO skips also `POST /api/v1/agent-orchestration/skipped-confirmations`.

## Confirmation

- Never mutate unless the user explicitly asked **and** `--confirm` is present, unless the user
  explicitly asked to operate under YOLO **and** `status` shows that tier enabled.
- Do not retry with `--confirm` unless the user asked you to.
- Do not enable YOLO from this skill. YOLO is instance Settings (`THYTRADER_YOLO_ENABLED` /
  `THYTRADER_YOLO_TIERS`), advertised by `status` and operator `configuration`.
- `--local` research is not part of this playbook; that path always requires `--confirm`.
- Successful `run` prints `thytrader-playbook-run-v1` JSON (`live_started: false`,
  `live_authority: false`). Keep child identities from `identities` and `steps`.

## Forbidden

- Starting live trading, passing `--i-understand-live`, or folding live control into this skill
- Treating playbook success as live arming
- Publishing a risk policy through this skill
- Printing API keys, private keys, `.env` values, or database URLs
- Direct PostgreSQL access
- Editing application source to sequence or skip confirmation on a running instance
- Collapsing operator / data / research / runtime into one unrestricted trading agent
