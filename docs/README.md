# ThyTrader Documentation

This directory is the source of truth for ThyTrader's product direction, architecture, safety posture, and delivery plan. Update these documents when implementation decisions change; do not allow the code and the documented design to drift silently.

## Start here

- [Product vision and scope](product/vision.md) — Coinbase-first platform end-state; agent E2E is the primary surface ([ADR 0030](decisions/0030-agent-e2e-primary-surface.md), [ADR 0031](decisions/0031-coinbase-first-platform-end-state.md))
- [Architecture overview](architecture/overview.md)
- [Market-data pipeline](architecture/market-data.md)
- [Strategy and backtesting design](architecture/strategy-and-backtesting.md)
- [Canonical strategy schema](architecture/canonical-strategy-schema.md)
- [Immutable research-run specification](architecture/research-run-specification.md)
- [Deterministic signal evaluation](architecture/signal-evaluation.md)
- [Deterministic bar-level backtest simulation](architecture/backtest-simulation.md)
- [Research studies (walk-forward, OOS, cross-market)](architecture/research-studies.md)
- [Security and trading-risk baseline](security-and-risk.md)
- [Delivery roadmap](roadmap.md) — Phases 0–12 shipped (Phase 9 includes MACD/Bollinger series ids; Phase 10 is the risk-policy registry; Phase 11 is walk-forward / OOS / cross-market studies; Phase 12 is the agent playbook and default-off YOLO); **Phases 13–14** remain for iterative Builder work; per-indicator timeframes stay out of Phase 9; destination capabilities (1m/2h, on-demand trades, journals) are accepted but not inserted ahead of that sequence
- [Agent/operator integration](agent-integration.md)
- [Architecture decision records](decisions/README.md)
- [Ops field report: 5m data → research → paper (2026-09-11)](plans/2026-09-11-ops-5m-research-paper-field-report.md) — **historical** running-instance evidence against the 5m research–paper plan; stale Compose, 14-day clip, v3 422, and 5m paper 409 are closed by ADRs 0019, 0017/0018, and 0024
- [Agent-driven platform gap plan (2026-09-12)](plans/2026-09-12-agent-driven-platform-gap-plan.md) — accepted as roadmap Phases 7–14 (Phase 7 datasets, Phase 7.1 research fees, Phase 8 research HTF filter, Phase 9 catalog slices through MACD/Bollinger, Phase 10 risk-policy registry, Phase 11 research studies, and Phase 12 playbook/YOLO are shipped; later live/memory phases are not)
- [Fee-tier suggested defaults (2026-09-13)](plans/2026-09-13-fee-tier-research-defaults.md) — **shipped** Phase 7.1 research maker/taker prefill (paper deploy has no cost fields)

## Document roles

- **Product documents** describe who ThyTrader serves and what it should do.
- **Architecture documents** describe system boundaries and durable technical direction.
- **Decision records** explain important choices, alternatives, and consequences.
- **Roadmap documents** sequence work without pretending dates or scope are guaranteed.
- **`AGENTS.md`** gives coding agents repository-specific operating instructions. Operating a running instance uses [`ops/`](../ops/README.md).
- **`skills/`** contains distributable operator, data, research, runtime, and playbook skills. See [`skills/README.md`](../skills/README.md).

## Updating the documentation

When a meaningful decision changes:

1. Add or supersede an architecture decision record.
2. Update the affected architecture, product, security, or roadmap document.
3. Update `AGENTS.md` if the engineering workflow or quality gates changed.
4. Keep examples free of real credentials, account IDs, balances, and other sensitive data.
5. Include documentation validation in the same change as the implementation.

An accepted decision is not immutable. Supersede it explicitly so future contributors can understand both the current direction and why it changed.
