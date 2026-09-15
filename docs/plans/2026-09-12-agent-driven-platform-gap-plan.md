# Agent-driven platform gap plan (2026-09-12)

**Status: accepted as roadmap Phases 7–14 (planned / not shipped unless marked complete).**
Definitive implementer sequence: [`docs/roadmap.md`](../roadmap.md).

## Intent

ThyTrader should become a local-first workstation that **agent operators can drive
end to end**: collect and heal market data, research and publish strategies, backtest
with honest assumptions, paper, then (when armed) live — across a **portfolio**, not
only a single asset, with multi-timeframe strategies and a growing indicator set.

Remote / SaaS exposure is **out of scope for this plan**.

Experiential memory / hindsight (operator-managed facts and lessons) is a **later**
concern, after the trading loop is trustworthy. Audit trails and immutable research
evidence are not that memory system.

## Shipped baseline (narrow but real)

- Skills: `thytrader-operator` (read-only), `thytrader-data` (watchlist / ingest /
  gaps), `thytrader-research` (draft → publish → backtest), `thytrader-runtime`
  (paper/live with `--confirm`; live also `--i-understand-live`).
- Market data: complete-only Parquet for **1h**, **5m**, **15m**, **30m**, **6h**, and **1d**; `inspect-gaps` /
  `fill-gaps`; no interpolation. Strategy / paper / live clocks stay `1h` or `5m` (live `1h`).
- Indicators: EMA, SMA, RSI, ATR, volume SMA, highest, lowest, stdev, ROC, Williams %R, CCI,
  identity OHLCV, and constant (Phase 9 first three slices). MACD/Bollinger and per-indicator
  timeframes are not shipped.
- Strategy: one instrument, long-only, max concurrent positions = 1.
- Execution: paper on 1h or 5m; live on **1h** only; single-position backtests.
- Fee **tier visibility** and research **suggested defaults** shipped
  ([fee-tier plan](2026-09-13-fee-tier-research-defaults.md)). Paper deploy has no maker/taker
  fields; paper keeps the documented `0.001` / `0.002` schedule.

See `docs/roadmap.md` Phases 0–6 for the completed vertical slice.

## Gaps relative to the intent

| Area | Gap |
|---|---|
| Agent E2E ease | Four skills + per-mutation `--confirm`; no orchestration playbook skill |
| Data coverage | Phase 7 shipped: 15m, 30m, 6h, and 1d datasets plus watch-completeness and stale-image hardening (not strategy clocks) |
| Fee UX | Research prefills suggested maker/taker; paper deploy still has no cost fields |
| Indicators | Tiny fail-closed catalog; Phase 9 first three slices added highest/lowest/stdev, roc/williams_r/cci, and identity/constant. No TA passthrough, no MACD/Bollinger, no per-indicator TF |
| Multi-timeframe | Research HTF filter + LTF entry shipped (ADR 0025). Paper/live still reject `htf_filter`. Per-indicator timeframes and `15m`/`30m`/`6h`/`1d` as LTF clocks remain later |
| Portfolio | No multi-position / cross-strategy risk registry or capital allocator |
| Research rigor | Walk-forward / OOS tooling and richer templates still deferred |
| Live extras | 5m live, trailing stops, user-order WS, native OCO deferred |
| Memory | Not in product docs; deferred by design |

## Planned: Safe mode vs YOLO mode

**Default remains Safe mode:** mutations require explicit `--confirm` (and live
start requires `--i-understand-live`). Observation skills stay read-only.

**YOLO mode (planned, default OFF):** an operator-enabled opt-in that lets agents
skip per-action confirmation on **allowed** surfaces so end-to-end automation is
easy when the operator wants that flexibility.

Design constraints (to implement later; not shipped):

1. Configuration / mode flag defaults to off; enabling is an explicit operator act.
2. Scope tiers: data + research may be YOLO-eligible; paper may be separately
   gated; **live start keeps a hard gate** even when YOLO is on unless a distinct
   live-YOLO arming step is explicitly designed and accepted.
3. Every skipped confirmation must write an audit event.
4. YOLO must never become the silent default of read-only observation skills.
5. Authority boundaries between operator / data / research / runtime stay separate;
   YOLO does not collapse skills into one unrestricted trading agent.

## Planned: Agent orchestration

A higher-level orchestration skill (or playbook) should sequence:

`data healthy → draft/publish → backtest → (optional) paper`

while calling the existing CLIs. It must not grant live authority by inheritance.
YOLO only changes confirmation friction inside allowed tiers.

## Build order (roadmap Phases 7–14)

1. **Phase 7** — Shipped: 15m/30m/6h/1d datasets and fail-closed agent data-loop hardening.
   These complete-only datasets are not strategy/paper/live clocks.
2. **Phase 7.1** — Fee-tier suggested defaults for research (shipped; paper had no cost fields).
3. **Phase 8** — Shipped (research HTF filter). Paper/live HTF evaluation remains later.
4. **Phase 9** — First three slices shipped (`highest`/`lowest`/`stdev`, `roc`/`williams_r`/`cci`,
   then `identity`/`constant`). Further kinds and multi-series outputs remain.
5. **Phase 10** — Portfolio + risk-policy registry.
6. **Phase 11** — Research rigor (walk-forward / OOS, templates).
7. **Phase 12** — Agent orchestration + YOLO opt-in.
8. **Phase 13** — Live extras (5m live, trailing, WS, OCO).
9. **Phase 14** — Memory / hindsight last.

## Non-goals (this plan)

- Hosted multi-tenant SaaS / remote-first exposure.
- Derivatives, leverage, multi-exchange.
- Unrestricted agent live trading without an explicit arming design.
- Shipping experiential memory before the core loop is trustworthy.

## Related docs

- `docs/roadmap.md` — definitive Phases 7–14
- `docs/plans/2026-09-13-fee-tier-research-defaults.md` — fee default design
- `docs/agent-integration.md` — safety model + planned YOLO
- `docs/product/vision.md` — product principles and V1 scope
- `docs/architecture/market-data.md`, `strategy-and-backtesting.md`,
  `canonical-strategy-schema.md` — shipped contracts
