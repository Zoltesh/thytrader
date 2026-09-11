---
name: thytrader-research
description: >-
  Create ThyTrader strategy drafts, publish immutable versions, and submit or
  compare deterministic backtests through the confirmation-gated thytrader-research
  CLI. Use when the user asks to create a strategy, publish, or run a backtest.
  Requires explicit --confirm for every mutation. Never deploys, paper-trades,
  live-trades, arms, or cancels orders.
---

# ThyTrader research

Bounded research mutations only. This skill is not an extension of `thytrader-operator` and has no paper, live, arming, cancellation, or kill-switch authority.

Default transport is the loopback HTTP API (`THYTRADER_API_BASE_URL` or `http://127.0.0.1:8200`). Pass `--local` only when you intentionally want PostgreSQL stores. Do not fall back from HTTP to the database if the API is down.

Existing HTTP contracts (`POST /api/v1/strategies`, `POST /api/v1/strategies/{id}/publish`, `POST /api/v1/backtests`) remain valid. The agent-facing mutation path is `uv run thytrader-research` with `--confirm`.

## Hard stop

When operating a running instance, do not edit `src/`, `compose.yaml`, Dockerfiles, Alembic, or tests.
Do not search the tree for a code patch. Report failures through this skill. Rebuild or restart only
with `make run` when the user asked, or when HTTP 404 on `/api/v1/strategies` or `/api/v1/backtests`
coincides with a ready `/health/ready` (stale Compose image). Open the `ops/` workspace instead of
the git root. Run every `uv run thytrader-*` command from the repository root (the parent of `ops/`).

## Commands

| Need | Command |
|---|---|
| Create the conservative reference draft | `uv run thytrader-research create-draft [--product-id ETH-USD] [--timeframe 5m] --confirm` |
| Save a draft from JSON | `uv run thytrader-research save-draft --file definition.json --revision N --confirm` |
| Publish the matching draft | `uv run thytrader-research publish --strategy-id UUID --confirm` |
| Submit an idempotent backtest | `uv run thytrader-research submit-backtest --file request.json --confirm` |
| List result summaries | `uv run thytrader-research list-results [--strategy-fingerprint sha256:…]` |
| Show one result summary | `uv run thytrader-research show-result --result-fingerprint sha256:…` |

`list-results` and `show-result` are read-only and do not use `--confirm`.

`create-draft` defaults to `BTC-USD` / `1h`. Pass `--product-id` and `--timeframe` (`1h` or `5m`) for
another USD spot product. Paper and live deployments still require `1h`.

`submit-backtest` may omit both `evaluation_start` and `evaluation_end`. The server fills the
dataset's usable window (warmup before the start, one bar after the end for next-open fill). If
supplied dates do not fit, the API returns 422 with a suggested ISO range. Do not invent a window
that the catalog cannot cover.

## Confirmation

- Never run `create-draft`, `save-draft`, `publish`, or `submit-backtest` unless the user explicitly asked for that mutation **and** `--confirm` is present.
- If `--confirm` is missing, the CLI exits without writing. Do not retry with `--confirm` unless the user asked you to.
- Successful mutations print JSON identities (`strategy_id`, `strategy_fingerprint`, `run_fingerprint`, `result_fingerprint`). Keep those identities.

## Forbidden

- Deployments, pause/resume/stop, Coinbase orders, risk-limit edits, kill switches
- Direct PostgreSQL access as the public agent contract
- Treating a backtest as a live or paper fill
- Archiving as part of this skill (out of scope)
- Editing application source to change strategy or backtest semantics on a running instance

Diagnose a running instance with `skills/thytrader-operator/SKILL.md` first when health is unknown. Coverage and ingest are `skills/thytrader-data/SKILL.md`. Paper/live control is `skills/thytrader-runtime/SKILL.md`. Strategy `timeframe` may be `1h` or `5m` for backtests; paper and live deployments still require `1h`.
