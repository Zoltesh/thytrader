---
name: thytrader-operator
description: >-
  Diagnose a running ThyTrader instance through the versioned read-only operator
  CLI and HTTP API. Use when checking health, configuration, Coinbase connectivity,
  market-data freshness, strategy/runtime status, backtest or paper/live performance,
  reconciliation, or a redacted support bundle. Never places, edits, or cancels
  orders and never arms live trading. `chat-status` reports whether an in-app LLM
  key is held in the API process; it never prints the key and is not Coinbase.
---

# ThyTrader operator (Cursor pointer)

Follow the repository skill of record at `skills/thytrader-operator/SKILL.md`. Do not invent commands, scrape logs, or query PostgreSQL.
