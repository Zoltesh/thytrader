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
- Market data: complete-only Parquet for **1h**, **5m**, and **15m**; `inspect-gaps` /
  `fill-gaps`; no interpolation. Strategy / paper / live clocks stay `1h` or `5m` (live `1h`).
- Indicators: EMA, SMA, RSI, ATR, volume SMA only.
- Strategy: one instrument, long-only, max concurrent positions = 1.
- Execution: paper on 1h or 5m; live on **1h** only; single-position backtests.
- Fee **tier visibility** shipped; fee-tier → research/paper **suggested defaults** planned
  ([fee-tier plan](2026-09-13-fee-tier-research-defaults.md)).

See `docs/roadmap.md` Phases 0–6 for the completed vertical slice.

## Gaps relative to the intent

| Area | Gap |
|---|---|
| Agent E2E ease | Four skills + per-mutation `--confirm`; no orchestration playbook skill |
| Data coverage | 30m / 6h / 1d deferred; 15m datasets shipped (not a strategy clock); agents babysit watchlist → ingest → gaps |
| Fee UX | Tier visible; not yet suggested defaults into research/paper |
| Indicators | Tiny fail-closed catalog; no broad TA passthrough |
| Multi-timeframe | One `timeframe` per strategy; no HTF filter + LTF entry semantics |
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

1. **Phase 7** — Remaining timeframes after 15m datasets: 30m, 6h, 1d + data-loop harden.
   15m complete-only ingest/publish/verify/catalog is shipped; it is not a strategy/paper/live clock.
2. **Phase 7.1** — Fee-tier suggested defaults for research/paper (parallel-friendly).
3. **Phase 8** — Multi-timeframe strategy semantics.
4. **Phase 9** — Wider fail-closed indicator catalog.
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
