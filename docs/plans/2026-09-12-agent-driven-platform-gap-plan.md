# Agent-driven platform gap plan (2026-09-12)

**Status: Phases 7–14 and sequenced destination slices through ADR 0045 are shipped.**
Remaining destination is the table in [`docs/roadmap.md`](../roadmap.md) (multi-instrument
documents, pyramiding, wider catalog, daily-loss/drawdown, rate limits/collars, paper fee fields,
ML, richer study catalog). Extra exchanges wait on an explicit yes.
[ADR 0046](../decisions/0046-shipped-vs-remaining-0031-destination.md) restates that `1m`/`2h`
clocks and on-demand **are** implemented; multi-instrument **documents** are not.
Definitive implementer sequence: [`docs/roadmap.md`](../roadmap.md).

## Intent

ThyTrader should become a local-first **Coinbase-first research and trading platform** that
**agent operators can drive end to end** as the primary product surface
([ADR 0030](../decisions/0030-agent-e2e-primary-surface.md),
[ADR 0031](../decisions/0031-coinbase-first-platform-end-state.md)): collect and heal market data,
research and publish strategies, backtest with honest assumptions, paper, then (when armed) live —
across a **portfolio**, not only a single asset, with multi-timeframe strategies and a growing
indicator set. A modern professional UI still matters; it is not a substitute for agent contracts.

Remote / SaaS exposure is **out of scope for this plan**. Additional exchanges wait until the
Coinbase spot path is trustworthy.

Experiential memory / hindsight (operator-managed facts and lessons, journals, sentiment, notify)
is **Phase 14** and is shipped as origin-attributed **hooks** (no model training). Audit trails and
immutable research evidence are not that memory system.

## Shipped baseline (narrow but real)

- Skills: `thytrader-operator` (read-only), `thytrader-data` (watchlist / ingest /
  gaps), `thytrader-research` (draft → publish → backtest), `thytrader-runtime`
  (paper/live with `--confirm`; live also `--i-understand-live`), `thytrader-playbook`
  (sequences those CLIs; never live).
- Market data: complete-only Parquet for **1h**, **5m**, **15m**, **30m**, **6h**, **1d**, **1m**,
  **2h**, and **4h**; `inspect-gaps` /
  `fill-gaps`; no interpolation. Strategy / paper / live clocks are every ingested venue TF
  ([ADR 0040](../decisions/0040-venue-strategy-paper-live-htf-clocks.md)).
- Indicators: EMA, SMA, RSI, ATR, volume SMA, highest, lowest, stdev, sample stdev, ROC, Williams %R, CCI,
  identity OHLCV, constant, WMA, momentum, MFI, MACD, Bollinger, stochastic, and ADX
  ([ADR 0047](../decisions/0047-wider-fail-closed-indicator-catalog.md)).
  Per-indicator timeframes are shipped ([ADR 0042](../decisions/0042-per-indicator-timeframes.md)).
- Strategy: one instrument, long or short, max concurrent positions = 1.
- Execution: paper and live on every ingested venue clock; single-position backtests.
- Fee **tier visibility** and research **suggested defaults** shipped
  ([fee-tier plan](2026-09-13-fee-tier-research-defaults.md)). Paper deploy has no maker/taker
  fields; paper keeps the documented `0.001` / `0.002` schedule.

See `docs/roadmap.md` Phases 0–6 for the completed vertical slice.

## Gaps relative to the intent

| Area | Gap |
|---|---|
| Agent E2E ease | Six skills: operator, data, research, runtime, playbook, plus `thytrader-memory`. Default remains `--confirm`. YOLO is shipped default-off (ADR 0034 / 0043) for data/research/paper/live `--confirm` skips; `--i-understand-live`, live place-order, `set-risk-policy`, and memory stay hard-gated. |
| Data coverage | Phase 7 shipped 15m/30m/6h/1d datasets plus watch-completeness. Remaining venue TFs `1m`/`2h`/`4h` have complete-only datasets ([ADR 0038](../decisions/0038-complete-only-1m-2h-4h-datasets.md)) and are strategy/paper/live/HTF clocks ([ADR 0040](../decisions/0040-venue-strategy-paper-live-htf-clocks.md)). |
| On-demand trades | ✅ Long or short discretionary orders with required SL/TP via order intent + risk; live attaches entry brackets when trailing is off ([ADR 0039](../decisions/0039-on-demand-discretionary-trades.md), [ADR 0045](../decisions/0045-spot-shorting-and-attached-entry-brackets.md)). Live shorts fail closed without available base. |
| Fee UX | Research prefills suggested maker/taker; paper deploy still has no cost fields |
| Indicators | Fail-closed catalog through ADR 0047 (stochastic, ADX, configurable rolling inputs, sample stdev) plus Phase 9 slices and optional per-indicator TFs (ADR 0042). No TA passthrough |
| Multi-timeframe | Research, paper, and live evaluate HTF filter + LTF entry (ADR 0025, ADR 0041). Venue LTF/HTF tokens widened by ADR 0040. Per-indicator timeframes shipped (ADR 0042) |
| Portfolio | Phase 10 shipped: typed registry, capital allocation, concurrent single-instrument paper/live. Intra-strategy pyramiding, multi-instrument strategy documents, and destination circuit breakers remain later. |
| Research rigor | Phase 11 shipped: OOS holdout, walk-forward validation, cross-market studies, richer templates, V1/V2/V3 matrix. Parameter sweeps, WFO, and stitched OOS equity shipped ([ADR 0044](../decisions/0044-parameter-sweeps-wfo-stitched-equity.md)). |
| Live extras | ✅ Phase 13: 5m live, ATR trailing, user-order WS, native OCO. Daily-loss kill remains destination |
| Memory | Phase 14 shipped: origin-attributed journals, sentiment/pattern hooks, operator monitor, and config-gated notify (ADR 0037). Bounded V1 trainer ships on those attributed local rows (ADR 0049): fail-closed, fingerprintable, advisory research input only. YOLO never covers this lane. Trade-reason review UI is a sibling. |

## Shipped: Safe mode vs YOLO mode

**Default remains Safe mode:** mutations require explicit `--confirm` (and live
start requires `--i-understand-live`). Observation skills stay read-only.

**YOLO mode (shipped, default OFF):** an operator-enabled opt-in that lets agents
skip per-action confirmation on **allowed** surfaces so end-to-end automation is
easy when the operator wants that flexibility. See
[ADR 0034](../decisions/0034-phase-12-agent-orchestration-yolo.md) and
[ADR 0043](../decisions/0043-yolo-live-skip-confirm.md).

Design constraints (shipped):

1. Configuration / mode flag defaults to off; enabling is an explicit operator act
   (`THYTRADER_YOLO_ENABLED` plus `THYTRADER_YOLO_TIERS`).
2. Scope tiers: data, research, paper, and live may be independently YOLO-eligible.
   Live YOLO skips `--confirm` on start/pause/resume/stop only. `--i-understand-live`,
   live place-order, `set-risk-policy`, `--local` research, and memory stay hard-gated.
   Paper YOLO never covers live.
3. Every skipped confirmation must write an audit event (`confirm_skipped`) or fail
   closed.
4. YOLO must never become the silent default of read-only observation skills.
5. Authority boundaries between operator / data / research / runtime stay separate;
   YOLO does not collapse skills into one unrestricted trading agent.
   The playbook never starts live.

## Shipped: Agent orchestration

`thytrader-playbook` sequences:

`data healthy → draft/publish → backtest → (optional) paper`

by calling the existing CLIs. It must not grant live authority by inheritance.
YOLO only changes confirmation friction inside allowed tiers.

## Build order (roadmap Phases 7–14) — all shipped

1. **Phase 7** — Shipped: 15m/30m/6h/1d datasets and fail-closed agent data-loop hardening.
   Those complete-only datasets were not strategy/paper/live clocks **in that slice**.
   [ADR 0040](../decisions/0040-venue-strategy-paper-live-htf-clocks.md) later made every ingested
   venue TF a strategy, paper, live, discretionary, and HTF clock.
2. **Phase 7.1** — Fee-tier suggested defaults for research (shipped; paper had no cost fields).
3. **Phase 8** — Shipped (research HTF filter). Paper/live HTF evaluation shipped later (ADR 0041).
4. **Phase 9** — Five catalog slices shipped (`highest`/`lowest`/`stdev`, `roc`/`williams_r`/`cci`,
   `identity`/`constant`, `wma`/`momentum`/`mfi`, then `macd`/`bollinger`). Per-indicator timeframes
   remain out of Phase 9; shipped later as ADR 0042.
5. **Phase 10** — Shipped: risk-policy registry and concurrent single-instrument paper/live.
6. **Phase 11** — Shipped: walk-forward / OOS / cross-market studies, richer templates, V1/V2/V3 matrix (ADR 0035).
7. **Phase 12** — Shipped: agent playbook + YOLO opt-in (ADR 0034). Live `--confirm`
   skip shipped later (ADR 0043).
8. **Phase 13** — Shipped: 5m live, ATR trailing, user-order WS, native OCO (ADR 0036).
9. **Phase 14** — Shipped: journals, sentiment/pattern hooks, monitor, config-gated notify
   (ADR 0037). No ML training.

## Non-goals (this plan)

- Hosted multi-tenant SaaS / remote-first exposure.
- Derivatives, leverage, additional exchanges before Coinbase spot is trustworthy.
- Unrestricted agent live trading without an explicit arming design.
- Shipping experiential memory before the core loop is trustworthy.
- Treating a polished UI as a substitute for agent-operable APIs and skills.
- Silently widening strategy/paper/live clocks when adding datasets (`1m`/`2h` included).
  [ADR 0040](../decisions/0040-venue-strategy-paper-live-htf-clocks.md) is the later clock ADR.

## Related docs

- `docs/roadmap.md` — definitive Phases 7–14
- `docs/plans/2026-09-13-fee-tier-research-defaults.md` — fee default design
- `docs/agent-integration.md` — safety model + shipped YOLO / playbook
- `docs/product/vision.md` — product end-state, agent primacy, and shipped vs destination
- `docs/decisions/0030-agent-e2e-primary-surface.md` — agent E2E as primary surface
- `docs/decisions/0031-coinbase-first-platform-end-state.md` — Coinbase-first platform destination
- `docs/decisions/0046-shipped-vs-remaining-0031-destination.md` — shipped vs remaining 0031 restatement
- `docs/architecture/market-data.md`, `strategy-and-backtesting.md`,
  `canonical-strategy-schema.md` — shipped contracts
