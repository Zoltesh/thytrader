---
name: thytrader-data
description: >-
  Manage ThyTrader market-data watchlists and complete-only ingest through the
  confirmation-gated thytrader-data CLI. Use when the user asks what data exists,
  to add a product or timeframe, inspect gaps, or fill gaps. Requires --confirm
  on every mutation. Never deploys, paper-trades, live-trades, arms, or cancels
  orders. Never interpolates prices; intervals without trades are flat no-trade bars.
---

# ThyTrader data (Cursor pointer)

Follow the repository skill of record at `skills/thytrader-data/SKILL.md`. Do not invent commands, scrape logs, query PostgreSQL, or interpolate missing candles.
