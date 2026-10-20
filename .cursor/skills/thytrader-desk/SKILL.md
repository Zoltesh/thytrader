---
name: thytrader-desk
description: >-
  Run ThyTrader paper agent desks through thytrader-desk: a paper cash pool, a product
  universe and caps that a model harness (Claude Code, Pi, Codex, Hermes, or any command)
  wakes on a cadence. Each wake ThyTrader writes a briefing, the model answers with one JSON
  decision (no_trade, open, close), and ThyTrader applies it to a fresh discretionary paper
  book under the caps and journals it. Use when the user asks to create, wake, schedule,
  pause, score or review an agent desk, or to let a model day-trade on paper. Mutations
  require --confirm. YOLO never covers this lane. Paper only: never live, never arms, never
  touches strategy bots or portfolios.
---

# ThyTrader agent desk (Cursor pointer)

Follow the repository skill of record at `skills/thytrader-desk/SKILL.md`. Do not skip `--confirm`, consult YOLO, or
fold this authority into the runtime or portfolio skills. Desks are paper only.
