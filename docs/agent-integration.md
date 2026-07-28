# Agent and Operator Integration

## Recommendation

ThyTrader should ship repository-level agent skills, but only after it exposes stable, versioned operational interfaces. The skill is documentation and workflow; it should not compensate for a missing product API by scraping logs, querying PostgreSQL directly, or importing private internals.

A `skills/` directory is reserved now so agent integration evolves as a first-class product surface rather than an afterthought.

## Initial use cases

A user's agent should be able to answer questions such as:

- Is the API, worker, database, and exchange connection healthy?
- Is market data current, complete, and free of known gaps?
- Are configured credentials present and usable, and what permissions were detected, without revealing them?
- Which strategies are running, paused, degraded, or blocked by risk controls?
- How has a strategy performed over a selected period?
- How much did fees, spread, and slippage contribute?
- Are live and backtest assumptions materially different?
- Did restarts, disconnects, rejected orders, or reconciliation anomalies occur?
- Which configuration values are invalid, risky, deprecated, or ineffective?

## Supported interface design

Prefer a versioned `thytrader` operator CLI backed by the same application services as a read-only HTTP API.

Potential command groups, to be implemented before referenced by a real skill:

- health summary;
- redacted configuration validation;
- exchange connectivity and permission validation;
- market-data freshness and gap report;
- strategy status and performance report;
- risk-state and recent-trigger report;
- order/fill reconciliation report;
- support bundle generation with deterministic redaction.

Outputs should support both human-readable text and a documented JSON schema. Commands should return meaningful exit codes so agents can distinguish healthy, degraded, and failed states.

## Safety model

The first operator contract and skill are read-only.

They must not:

- print API keys or private-key material;
- expose raw environment values;
- include unnecessarily precise account identifiers in support bundles;
- place, edit, or cancel orders;
- arm live trading;
- change strategy/risk configuration;
- bypass the API by reading or mutating database tables;
- treat missing telemetry as proof of health.

Future mutation tools must be separate, narrowly scoped, auditable, idempotent where applicable, and confirmation-gated. A read-only skill must never silently gain mutation capabilities.

## Stable diagnostics schema

Every machine-readable report should include:

- schema version;
- application version;
- timestamp and timezone;
- overall status: healthy, degraded, or failed;
- component statuses and stable reason codes;
- data freshness/coverage metadata;
- redaction metadata;
- partial-result warnings;
- recommended next diagnostic action.

Performance reports must state timeframe, strategy version, dataset/source, currency, fee treatment, and whether values are backtest, paper, or live.

## Skill packaging

The intended layout is:

```text
skills/
└── thytrader-operator/
    ├── SKILL.md
    └── references/
        ├── diagnostics-api.md
        └── report-schemas.md
```

Do not add `SKILL.md` until its required commands/endpoints exist and are exercised in tests. Shipping a plausible but nonfunctional skill would create a false operational contract.

The future skill should tell agents to:

1. Verify version and connectivity.
2. Start with read-only health/configuration checks.
3. Gather the minimum required report.
4. Preserve mode, timeframe, and strategy-version context.
5. Correlate performance with fees, data quality, risk events, and execution anomalies.
6. Redact before returning diagnostics.
7. Clearly separate verified findings from hypotheses.
8. Stop and request explicit authority before any state-changing action.

## Testing requirements

- Contract tests for every JSON report.
- Golden tests for redaction.
- Tests proving secrets cannot appear in output.
- Compatibility tests between the skill's documented schema and current CLI/API.
- Failure-mode tests for database, worker, Coinbase, and market-data outages.
- Tests proving read-only commands cannot mutate orders, strategies, or runtime state.
## Skill evolution by phase

| Phase | Supported agent authority |
|---|---|
| Current (Phases 0–1) | Read-only health, configuration validity, snapshot/history freshness, redacted diagnostics |
| Phase 2 | Read-only market-data quality and strategy-schema validation/reporting |
| Phase 3 | Read-only backtest inspection and reproducibility reports |
| Phase 4 | Read-only paper-session health and performance |
| Phase 5+ | Separate mutation tools only if justified; arming, cancellation, configuration changes, and kill switches always explicit confirmation-gated |

The key principle: **agents should diagnose and explain first; trading authority is not a natural extension of observability.**
