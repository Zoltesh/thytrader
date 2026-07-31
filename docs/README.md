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
- [Security and trading-risk baseline](security-and-risk.md)
- [Delivery roadmap](roadmap.md)
- [Agent/operator integration](agent-integration.md)
- [Architecture decision records](decisions/README.md)

## Document roles

- **Product documents** describe who ThyTrader serves and what it should do.
- **Architecture documents** describe system boundaries and durable technical direction.
- **Decision records** explain important choices, alternatives, and consequences.
- **Roadmap documents** sequence work without pretending dates or scope are guaranteed.
- **`AGENTS.md`** gives coding agents repository-specific operating instructions.
- **`skills/`** will contain distributable agent workflows once ThyTrader exposes stable operational contracts. No skill exists yet; see [`skills/README.md`](../skills/README.md) for the creation gate.

## Updating the documentation

When a meaningful decision changes:

1. Add or supersede an architecture decision record.
2. Update the affected architecture, product, security, or roadmap document.
3. Update `AGENTS.md` if the engineering workflow or quality gates changed.
4. Keep examples free of real credentials, account IDs, balances, and other sensitive data.
5. Include documentation validation in the same change as the implementation.

An accepted decision is not immutable. Supersede it explicitly so future contributors can understand both the current direction and why it changed.
