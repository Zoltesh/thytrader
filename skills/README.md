# ThyTrader Agent Skills

Distributable skills that operate a running ThyTrader instance through supported, versioned interfaces. They must not scrape logs, query PostgreSQL, or import private internals.

## `thytrader-operator`

Read-only diagnostics: health, redacted configuration, exchange permissions, market-data freshness, strategy/runtime status, performance, reconciliation, and a redacted support bundle.

- Skill: [`thytrader-operator/SKILL.md`](thytrader-operator/SKILL.md)
- CLI: `uv run thytrader-operator`
- HTTP: `GET /api/v1/operator/...`

## `thytrader-research`

Confirmation-gated drafts, immutable publication, and idempotent backtest submission. No paper, live, arming, or cancellation authority.

- Skill: [`thytrader-research/SKILL.md`](thytrader-research/SKILL.md)
- CLI: `uv run thytrader-research … --confirm`

See [`docs/agent-integration.md`](../docs/agent-integration.md) for the safety model.

## Skill policy

- Read-only observation comes first.
- Do not expose secrets or raw environment values.
- Do not make direct database access part of the public agent contract.
- Distinguish backtest, paper, and live data in every report.
- State timeframe, currency, strategy version, and data completeness.
- Separate verified findings from hypotheses.
- Keep state-changing tools separate, narrowly scoped, auditable, and explicitly confirmation-gated.
