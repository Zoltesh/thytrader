# ThyTrader Documentation

This directory is the source of truth for ThyTrader's product direction, architecture, safety posture, and delivery plan. Update these documents when implementation decisions change; do not allow the code and the documented design to drift silently.

## Start here

- [Product vision and scope](product/vision.md)
- [Architecture overview](architecture/overview.md)
- [Market-data pipeline](architecture/market-data.md)
- [Strategy and backtesting design](architecture/strategy-and-backtesting.md)
- [Canonical strategy schema](architecture/canonical-strategy-schema.md)
- [Immutable research-run specification](architecture/research-run-specification.md)
- [Deterministic signal evaluation](architecture/signal-evaluation.md)
- [Deterministic bar-level backtest simulation](architecture/backtest-simulation.md)
- [Security and trading-risk baseline](security-and-risk.md)
- [Delivery roadmap](roadmap.md)
- [Agent/operator integration](agent-integration.md)
- [Architecture decision records](decisions/README.md)
- [Ops field report: 5m data → research → paper (2026-09-11)](plans/2026-09-11-ops-5m-research-paper-field-report.md) — running-instance evidence against the 5m research–paper plan (stale Compose, 14-day clip, v3 422, 5m paper 409)

## Document roles

- **Product documents** describe who ThyTrader serves and what it should do.
- **Architecture documents** describe system boundaries and durable technical direction.
- **Decision records** explain important choices, alternatives, and consequences.
- **Roadmap documents** sequence work without pretending dates or scope are guaranteed.
- **`AGENTS.md`** gives coding agents repository-specific operating instructions. Operating a running instance uses [`ops/`](../ops/README.md).
- **`skills/`** contains distributable operator, data, research, and runtime skills. See [`skills/README.md`](../skills/README.md).

## Updating the documentation

When a meaningful decision changes:

1. Add or supersede an architecture decision record.
2. Update the affected architecture, product, security, or roadmap document.
3. Update `AGENTS.md` if the engineering workflow or quality gates changed.
4. Keep examples free of real credentials, account IDs, balances, and other sensitive data.
5. Include documentation validation in the same change as the implementation.

An accepted decision is not immutable. Supersede it explicitly so future contributors can understand both the current direction and why it changed.
