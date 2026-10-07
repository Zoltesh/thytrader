---
name: thytrader-research
description: >-
  Create, edit, clone, import, and delete ThyTrader strategies (one mutable
  object per strategy, revision-guarded saves) and submit or compare
  deterministic backtests and composed research studies by strategy id through
  the confirmation-gated thytrader-research CLI. Use when the user asks to
  create or change a strategy, delete strategies, run a backtest, or run an OOS /
  walk-forward / cross-market / parameter-sweep / WFO study, or to list persisted
  study catalog rows. Requires explicit --confirm for every mutation.
  Never deploys, paper-trades, live-trades, arms, or cancels orders.
---

# ThyTrader research (Cursor pointer)

Follow the repository skill of record at `skills/thytrader-research/SKILL.md`. Do not invent commands or skip `--confirm`.
