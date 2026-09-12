# Agent-Driven Platform Gap Plan

> Status: **planned / proposed** (docs-only). Captures Brayden's product direction and the remaining
> capability gaps relative to what is shipped today.
> Branch baseline: `main` at `7c8e69d`.
> Does **not** claim YOLO mode, multi-timeframe combined strategies, multi-asset portfolio risk, or
> experiential memory as implemented.

## 1. Goals

Agent operators should drive ThyTrader end-to-end easily: collect missing data, ensure no gaps, compute
indicators, evaluate strategies across multiple timeframes (individually and combined), and manage an
entire portfolio rather than a single asset.

The longer product ambition is a comprehensive auto-trader that applies proven strategies, with a
framework that can learn and grow. Experiential memory / hindsight remains deferred until the core
trading loop (data → research → paper → guarded live) is trustworthy.

**Remote-safe exposure is explicitly not a priority right now.** Do not expand remote-access or hosted
SaaS work in this plan.

## 2. Shipped baseline (narrow — do not overstate)

As of the branch baseline, ThyTrader ships a narrow vertical slice:

| Area | Shipped today |
|---|---|
| Agent surfaces | Operator, data, research, and runtime skills/CLIs with clear authority boundaries |
| Market data | Complete-only `1h` and `5m` datasets; inspect/fill gaps; no interpolation |
| Indicators | EMA, SMA, RSI, ATR, volume SMA (fail-closed registry) |
| Strategy / position | Single-instrument, long-only; one `timeframe` per strategy |
| Execution clocks | Live `1h`; paper + research `1h` or `5m` |
| Backtests | Single-position V1/V2/V3 engines; deterministic, immutable results |
| Safety default | Confirmation-gated mutations (`--confirm`); live start also requires `--i-understand-live` |

This is enough for a careful operator or agent to research and paper-trade one published strategy on
one product. It is **not** yet an agent-driven multi-asset portfolio platform.

## 3. Product direction (Brayden)

1. **E2E agent operability** — an agent should complete the full loop without friction that exists only
   because authority is split across four CLIs, while still preserving those boundaries.
2. **Data completeness first** — agents collect missing history, inspect gaps, and refuse to trade on
   incomplete islands.
3. **Multi-timeframe** — use timeframes individually today; combine HTF+LTF conditions as a first-class
   strategy semantic (planned).
4. **Portfolio scope** — manage exposure and capital across assets and strategies, not one position at a
   time.
5. **Proven strategies, then learning** — auto-apply researched strategies; memory/hindsight only after
   the loop is green.
6. **Local-first posture** — remote-safe exposure stays out of scope for this plan.

## 4. YOLO mode (NEW — planned opt-in)

**Default remains confirmation-gated.** Every mutation skill/CLI keeps `--confirm` (or equivalent) as
the safe path. Observation skills stay read-only.

**YOLO / no-confirm mode** is an **operator-enabled opt-in** so agents can skip per-action confirmation
on allowed surfaces and complete E2E work without repetitive gates. It is **not shipped**. Document it
as planned in agent-integration and skills policy.

### Proposed safety design (not implemented)

| Control | Proposal |
|---|---|
| Config / mode flag | Explicit operator arming; **default OFF** |
| Scope tiers | e.g. data + research may be YOLO-eligible; paper control may be a separate tier |
| Live hard gate | Live start still requires `--i-understand-live` (or equivalent) even when YOLO is on, unless a **separate** live-yolo arming flag is explicitly set |
| Audit | Every skipped confirmation records an audit event (who/what/when/surface) |
| Observation purity | YOLO must **never** become the silent default of `thytrader-operator` or other read-only skills |
| Skill docs | Dual-mode (safe vs yolo) called out as planned; shipped skills stay confirm-gated until code lands |

YOLO reduces friction; it does not collapse research, paper, and live into one skill, and it does not
grant trading authority to observation.

## 5. Gap plan — sequenced roadmap

Documented as **next work**, not complete. Order is intentional: trustworthy data and strategy
semantics before portfolio risk, agent orchestration, and live extras.

1. **Finish Phase 2A timeframes + harden agent data loop**  
   Add `15m`, `30m`, `6h`, `1d` under the same complete-only contract. Clarify `watch_complete` vs
   island `complete` so agents do not confuse lookback coverage with publication success.

2. **Multi-timeframe strategy semantics**  
   Schema + engines for HTF+LTF combined conditions. Today each strategy has one `timeframe`. Combined
   semantics are planned; do not document them as available.

3. **Widen indicator catalog**  
   Expand the fail-closed deterministic registry beyond EMA/SMA/RSI/ATR/volume SMA. Unknown indicators
   continue to reject publication.

4. **Portfolio + risk registry**  
   Multi-position support, cross-strategy exposure, and capital allocation across assets. Replaces the
   single-position long-only ceiling for portfolio ambition.

5. **Research rigor tooling**  
   Walk-forward / out-of-sample workflows, reusable templates, and a clearer engine support matrix so
   agents and humans know what each engine actually consumes.

6. **Agent orchestration skill(s) + YOLO opt-in**  
   Thin orchestration on top of the four CLIs for E2E ease **without** collapsing authority boundaries.
   Include the YOLO design above as operator posture, default off.

7. **Live extras (after paper/restart/reconcile stay green)**  
   `5m` live, trailing stops, user-order WebSockets, native OCO/brackets — only after the shared
   worker path remains healthy under restart, stale-data, and reconciliation drills.

8. **Memory / hindsight (later)**  
   Experiential memory is not a substitute for gaps 1–7. Defer until the core loop is trustworthy.

## 6. Build order (summary)

```text
2A timeframes + data-loop clarity
        → multi-TF strategy semantics
        → indicator catalog widen
        → portfolio + risk registry
        → research rigor (walk-forward/OOS/templates)
        → orchestration skill + YOLO opt-in (default off)
        → live extras (5m live, trailing, WS, OCO)
        → memory/hindsight (later)
```

Each wave should ship as a usable, tested increment with docs that keep shipped vs planned explicit.

## 7. Non-goals (this plan)

- Remote-safe exposure, hosted multi-tenant SaaS, or expanding public network surface.
- Claiming YOLO, multi-TF combined strategies, or portfolio risk as already implemented.
- Experiential memory / hindsight as a near-term substitute for data, research, or execution gaps.
- Collapsing operator / data / research / runtime authority into one unconstrained agent surface.
- Derivatives, leverage, multi-exchange, or HFT claims.

## 8. Doc touchpoints

| Document | Role |
|---|---|
| This plan | Primary gap + YOLO design |
| [`docs/roadmap.md`](../roadmap.md) | Next capability waves pointer |
| [`docs/agent-integration.md`](../agent-integration.md) | Planned dual-mode (safe vs yolo) + orchestration |
| [`docs/product/vision.md`](../product/vision.md) | Direction language for portfolio + agent E2E |
| Architecture overviews | Short “Future direction” notes only |
| [`skills/README.md`](../../skills/README.md) | Planned YOLO + orchestration note |

## 9. Success criteria for later implementation PRs

Implementation work that closes these gaps should:

- keep confirmation-gated mode as the default;
- leave YOLO off unless explicitly armed, with audit of skipped confirms;
- keep live start hard-gated even under YOLO unless a separate live-yolo arming exists;
- refuse incomplete data and unknown indicators (fail closed);
- update this plan and the roadmap wave list when a wave actually ships.
