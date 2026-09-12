# Agent-driven platform gap plan (2026-09-12)

**Status: planned / proposed.** Nothing in this document is shipped unless the live
roadmap or architecture docs already mark it complete.

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
- Market data: complete-only Parquet for **1h** and **5m**; `inspect-gaps` /
  `fill-gaps`; no interpolation.
- Indicators: EMA, SMA, RSI, ATR, volume SMA only.
- Strategy: one instrument, long-only, max concurrent positions = 1.
- Execution: paper on 1h or 5m; live on **1h** only; single-position backtests.

See `docs/roadmap.md` Phases 0–6 for the completed vertical slice.

## Gaps relative to the intent

| Area | Gap |
|---|---|
| Agent E2E ease | Four skills + per-mutation `--confirm`; no orchestration playbook skill |
| Data coverage | 15m / 30m / 6h / 1d deferred; agents babysit watchlist → ingest → gaps |
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

Document CLIs and skills as supporting both postures once the flag exists.

## Planned: Agent orchestration

A higher-level orchestration skill (or playbook) should sequence:

`data healthy → draft/publish → backtest → (optional) paper`

while calling the existing CLIs. It must not grant live authority by inheritance.
YOLO only changes confirmation friction inside allowed tiers.

## Build order

1. Finish Phase 2A timeframes (15m, 30m, 6h, 1d) and harden the agent data loop
   (`watch_complete` clarity, fewer stale-image footguns).
2. Multi-timeframe strategy semantics (schema + engines for combined TFs).
3. Widen the indicator catalog under the same fail-closed deterministic contract.
4. Portfolio + risk-policy registry (multi-position, cross-strategy exposure,
   capital allocation).
5. Research rigor: walk-forward / OOS, templates, clearer engine-support matrix.
6. Agent orchestration skill + YOLO opt-in (safe default; live hard-gated).
7. Live extras (5m live, trailing, WS, OCO) after paper/restart/reconcile stays green.
8. Memory / hindsight last.

## Non-goals (this plan)

- Hosted multi-tenant SaaS / remote-first exposure.
- Derivatives, leverage, multi-exchange.
- Unrestricted agent live trading without an explicit arming design.
- Shipping experiential memory before the core loop is trustworthy.

## Related docs

- `docs/roadmap.md` — capability waves pointer
- `docs/agent-integration.md` — safety model + planned YOLO
- `docs/product/vision.md` — product principles and V1 scope
- `docs/architecture/market-data.md`, `strategy-and-backtesting.md`,
  `canonical-strategy-schema.md` — shipped contracts
