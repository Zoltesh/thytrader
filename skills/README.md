# ThyTrader Agent Skills

This directory is reserved for distributable skills that help user-controlled agents interact with ThyTrader through supported, versioned interfaces.

## Planned first skill

`thytrader-operator` will provide a read-only workflow for:

- component and exchange-connection health;
- redacted configuration validation;
- market-data freshness and gap analysis;
- strategy runtime state;
- backtest, paper, and live performance analysis;
- fees, spread, slippage, and execution-quality analysis;
- risk-policy events and reconciliation anomalies;
- generation of a redacted diagnostic/support bundle.

See [`docs/agent-integration.md`](../docs/agent-integration.md) for the product contract and safety model.

## Why there is no `SKILL.md` yet

A skill must describe commands and schemas that actually exist. ThyTrader does not yet expose a stable diagnostics API or CLI, so publishing an executable-looking skill now would create a false contract and encourage agents to scrape logs or query PostgreSQL directly.

The `thytrader-operator/SKILL.md` file should be added only after:

1. the read-only operator API/CLI is implemented;
2. its JSON schemas and exit codes are versioned;
3. redaction and non-mutation tests pass;
4. the documented commands are exercised end to end;
5. compatibility checks can detect drift between the skill and application.

## Skill policy

- Read-only observation comes first.
- Do not expose secrets or raw environment values.
- Do not make direct database access part of the public agent contract.
- Distinguish backtest, paper, and live data in every report.
- State timeframe, currency, strategy version, and data completeness.
- Separate verified findings from hypotheses.
- Keep state-changing tools separate, narrowly scoped, auditable, and explicitly confirmation-gated.
