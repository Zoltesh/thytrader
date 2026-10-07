---
name: thytrader-runtime
description: >-
  Start, pause, resume, or stop ThyTrader paper and live deployments and
  whole portfolios (portfolio-start/pause/resume/stop, one bot per sleeve, and
  portfolio-reset-breaker), read their per-bar decision timeline (read-only
  `decisions`), publish the risk-policy registry, run fleet controls
  (fleet-preview/status/disarm/stop/flatten/rearm), link paper/live twins, set
  YAML settings, and show/set/clear write-only Coinbase credentials, through the
  confirmation-gated thytrader-runtime CLI. Use when the user
  explicitly asks to deploy, pause, resume, stop, place an on-demand order, set
  the risk policy, or manage Coinbase API secrets. Requires --confirm on every
  mutation unless YOLO covers that tier. Live start, live resume, and live
  place-order also require --i-understand-live (sent as HTTP i_understand_live=true).
  YOLO live may skip --confirm on start/pause/resume/stop
  only. Credential set/clear always need --confirm; YOLO never covers them.
  Publishing a risk policy or setting credentials does not arm live trading.
  Never diagnose through this skill and never submit Coinbase orders directly.
---

# ThyTrader runtime (Cursor pointer)

Follow the repository skill of record at `skills/thytrader-runtime/SKILL.md`. Do not fold this authority into operator or research skills.
