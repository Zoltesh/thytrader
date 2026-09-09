---
name: thytrader-runtime
description: >-
  Start, pause, resume, or stop ThyTrader paper and live deployments through the
  confirmation-gated thytrader-runtime CLI. Use when the user explicitly asks to
  deploy, pause, resume, or stop a paper or live runtime. Requires --confirm on
  every mutation. Live start also requires --i-understand-live. Never diagnose
  through this skill and never submit Coinbase orders directly.
---

# ThyTrader runtime

Confirmation-gated paper and live **control**. This skill is not an extension of `thytrader-operator` or `thytrader-research`.

HTTP-only against the loopback API (`THYTRADER_API_BASE_URL` or `http://127.0.0.1:8200`). There is no `--local` database mode.

Live trading spends real money. Do not start live unless the user explicitly asked to arm live trading.

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

`list` and `show` are read-only and do not use `--confirm`.

Underlying HTTP:

- `GET/POST /api/v1/deployments`
- `POST /api/v1/deployments/{id}/pause`
- `POST /api/v1/deployments/{id}/resume`
- `POST /api/v1/deployments/{id}/stop`

## Confirmation

- Never mutate unless the user explicitly asked **and** `--confirm` is present.
- Never start live without both `--confirm` and `--i-understand-live`.
- If a required flag is missing, the CLI exits without writing. Do not retry with extra flags unless the user asked you to.
- Successful mutations print JSON identities (`id`, `mode`, `status`, `strategy_fingerprint`). Keep those identities.
- Watch status after a mutation with `uv run thytrader-operator runtime --deployment-id UUID`.

## Forbidden

- Using this skill because you can observe a runtime
- Folding these commands into operator or research skills
- Printing API keys, private keys, `.env` values, or database URLs
- Direct PostgreSQL access
- Cancelling individual Coinbase orders or changing risk-policy configuration (out of scope)
- Treating a timeout as proof the start/pause/stop failed; `show` the deployment and reconcile before retrying
