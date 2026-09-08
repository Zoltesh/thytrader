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

Existing HTTP contracts (`POST /api/v1/strategies`, `POST /api/v1/strategies/{id}/publish`, `POST /api/v1/backtests`) remain valid. The agent-facing mutation path is `uv run thytrader-research` with `--confirm`.

## Commands

| Need | Command |
|---|---|
| Create the conservative reference draft | `uv run thytrader-research create-draft --confirm` |
| Save a draft from JSON | `uv run thytrader-research save-draft --file definition.json --revision N --confirm` |
| Publish the matching draft | `uv run thytrader-research publish --strategy-id UUID --confirm` |
| Submit an idempotent backtest | `uv run thytrader-research submit-backtest --file request.json --confirm` |
| List result summaries | `uv run thytrader-research list-results [--strategy-fingerprint sha256:…]` |
| Show one result summary | `uv run thytrader-research show-result --result-fingerprint sha256:…` |

`list-results` and `show-result` are read-only and do not use `--confirm`.

## Confirmation

- Never run `create-draft`, `save-draft`, `publish`, or `submit-backtest` unless the user explicitly asked for that mutation **and** `--confirm` is present.
- If `--confirm` is missing, the CLI exits without writing. Do not retry with `--confirm` unless the user asked you to.
- Successful mutations print JSON identities (`strategy_id`, `strategy_fingerprint`, `run_fingerprint`, `result_fingerprint`). Keep those identities.

## Forbidden

- Deployments, pause/resume/stop, Coinbase orders, risk-limit edits, kill switches
- Direct PostgreSQL access
- Treating a backtest as a live or paper fill
- Archiving as part of this skill (out of scope)

Diagnose a running instance with `skills/thytrader-operator/SKILL.md` first when health is unknown.
