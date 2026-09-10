# ThyTrader Agent Skills

Distributable skills that operate a running ThyTrader instance through supported, versioned interfaces. They must not scrape logs, query PostgreSQL, or import private internals.

Product of record is this directory. Cursor auto-discovery pointers live under `.cursor/skills/` and must not diverge from these contracts.

## `thytrader-operator`

Read-only diagnostics: health, redacted configuration, exchange permissions, market-data freshness, strategy/runtime status, performance, reconciliation, and a redacted support bundle.

- Skill: [`thytrader-operator/SKILL.md`](thytrader-operator/SKILL.md)
- CLI: `uv run thytrader-operator` (HTTP by default; `--local` is explicit)
- HTTP: `GET /api/v1/operator/...`

## `thytrader-data`

Confirmation-gated watchlist, complete-only ingest, and gap inspection. No paper, live, strategy, or backtest authority. Does not interpolate missing candles.

- Skill: [`thytrader-data/SKILL.md`](thytrader-data/SKILL.md)
- CLI: `uv run thytrader-data … --confirm`
- HTTP: `/api/v1/data`

## `thytrader-research`

Confirmation-gated drafts, immutable publication, and idempotent backtest submission. No paper, live, arming, or cancellation authority.

- Skill: [`thytrader-research/SKILL.md`](thytrader-research/SKILL.md)
- CLI: `uv run thytrader-research … --confirm` (HTTP by default; `--local` is explicit)

## `thytrader-runtime`

Confirmation-gated paper and live deployment control. Live start also requires `--i-understand-live`. Not an extension of operator or research.

- Skill: [`thytrader-runtime/SKILL.md`](thytrader-runtime/SKILL.md)
- CLI: `uv run thytrader-runtime`
- HTTP: `/api/v1/deployments`

See [`docs/agent-integration.md`](../docs/agent-integration.md) for the safety model.

## Skill policy

- Read-only observation comes first.
- Do not expose secrets or raw environment values.
- Do not make direct database access part of the public agent contract.
- Distinguish backtest, paper, and live data in every report.
- State timeframe, currency, strategy version, and data completeness.
- Separate verified findings from hypotheses.
- Keep state-changing tools separate, narrowly scoped, auditable, and explicitly confirmation-gated.
- Do not silently fall back from HTTP to PostgreSQL when the API is unreachable.
