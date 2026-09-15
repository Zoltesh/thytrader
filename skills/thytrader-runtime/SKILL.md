---
name: thytrader-runtime
description: >-
  Start, pause, resume, or stop ThyTrader paper and live deployments, and publish
  the risk-policy registry, through the confirmation-gated thytrader-runtime CLI.
  Use when the user explicitly asks to deploy, pause, resume, stop, or set the
  risk policy. Requires --confirm on every mutation. Live start also requires
  --i-understand-live. Publishing a risk policy does not arm live trading.
  Never diagnose through this skill and never submit Coinbase orders directly.
---

# ThyTrader runtime

Confirmation-gated paper and live **control**, including the risk-policy registry. This skill is not an extension of `thytrader-operator` or `thytrader-research`.

HTTP-only against the loopback API (`THYTRADER_API_BASE_URL` or `http://127.0.0.1:8200`). There is no `--local` database mode.

Live trading spends real money. Do not start live unless the user explicitly asked to arm live trading.

Paper may start on closed **1h or 5m** bars of a published strategy. Live stays on closed **1h** bars. Do not start 5m live. Do not start paper or live on a published strategy that declares `htf_filter`; the API rejects those deployments.

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
| List deployments | `uv run thytrader-runtime list` |
| Show one snapshot | `uv run thytrader-runtime show UUID` |
| Start paper | `uv run thytrader-runtime start --strategy-fingerprint sha256:… --mode paper --cash 10000 --confirm` |
| Start live | `uv run thytrader-runtime start --strategy-fingerprint sha256:… --mode live --confirm --i-understand-live` |
| Pause | `uv run thytrader-runtime pause UUID --confirm` |
| Resume | `uv run thytrader-runtime resume UUID --confirm` |
| Stop | `uv run thytrader-runtime stop UUID --confirm` |
| Show risk policy | `uv run thytrader-runtime show-risk-policy` |
| Publish risk policy | `uv run thytrader-runtime set-risk-policy --max-concurrent-running-deployments 8 --max-concurrent-open-positions 8 --max-portfolio-exposure-fraction 1 --per-product-max-exposure-fraction 1 --paper-capital-quote 100000 --confirm` |

`list`, `show`, and `show-risk-policy` are read-only and do not use `--confirm`. Optional
`--product-allowlist BASE-USD` and `--allocation STRATEGY_UUID:QUOTE` may be repeated.
`set-risk-policy` requires `--confirm` and does **not** require `--i-understand-live`.

Underlying HTTP:

- `GET/POST /api/v1/deployments`
- `POST /api/v1/deployments/{id}/pause`
- `POST /api/v1/deployments/{id}/resume`
- `POST /api/v1/deployments/{id}/stop`
- `GET/PUT /api/v1/risk-policy`

## Confirmation

- Never mutate unless the user explicitly asked **and** `--confirm` is present, unless the user
  explicitly asked to operate under YOLO **and** the mutation is paper (not live, not
  `set-risk-policy`) **and** operator `configuration` / `thytrader-playbook status` shows the `paper`
  tier enabled.
- Never start live without both `--confirm` and `--i-understand-live`. Live and `set-risk-policy`
  never YOLO.
- If a required flag is missing, the CLI exits without writing. Do not retry with extra flags unless the user asked you to.
- Successful mutations print JSON identities (`id`, `mode`, `status`, `strategy_fingerprint`). Keep those identities.
- Watch status after a mutation with `uv run thytrader-operator runtime --deployment-id UUID`.

## Forbidden

- Using this skill because you can observe a runtime
- Folding these commands into operator or research skills
- Printing API keys, private keys, `.env` values, or database URLs
- Direct PostgreSQL access
- Cancelling individual Coinbase orders
- Publishing a risk policy without `--confirm`, or treating that mutation as live arming
- Treating a timeout as proof the start/pause/stop failed; `show` the deployment and reconcile before retrying
- Editing application source to arm, pause, or change execution on a running instance
