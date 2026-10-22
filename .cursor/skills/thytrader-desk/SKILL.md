---
name: thytrader-desk
description: >-
  Run ThyTrader agent desks through thytrader-desk: a cash pool, a product universe and caps
  that a model harness (Claude Code, Pi, Codex, Hermes, or any command) wakes on a cadence.
  Each wake ThyTrader writes a briefing, the model answers with one JSON decision (no_trade,
  open, close), and ThyTrader applies it to a fresh discretionary book under the caps and
  journals it. Desks are paper unless created live with --i-understand-live and reserved in the
  risk policy's desk_allocations. Use when the user asks to create, wake, schedule, pause,
  score or review an agent desk, or to let a model trade. Mutations require --confirm. YOLO
  never covers this lane. Never arms strategy bots or touches portfolios.
---

# ThyTrader agent desk (Cursor pointer)

Follow the repository skill of record at `skills/thytrader-desk/SKILL.md`. Do not skip `--confirm`, consult YOLO, or
fold this authority into the runtime or portfolio skills. A live desk needs the live
acknowledgement and a risk-policy desk reservation.
