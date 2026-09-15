# 0005: Canonical versioned strategy schema

- Status: Accepted
- Date: 2026-07-26
- Related: [0031](0031-coinbase-first-platform-end-state.md) records destination venue timeframes
  and single- plus multi-asset deploy. This ADR's one-schema-across-runtimes decision is unchanged;
  schema widening still requires a later ADR.

## Context

ThyTrader must support UI-authored strategies, templates, credible backtests, paper trading, and live execution. Separate representations for each runtime would drift and make results irreproducible. A node canvas in V1 would add complexity before the domain model is proven.

## Decision

Define one backend-validated, immutable, versioned declarative strategy schema. It represents indicators, nested conditions, sizing, execution, exits, and risk settings without embedding UI concerns.

V1 uses a structured rule builder with nested AND/OR groups. Templates produce the same schema. Backtest, paper, and live runtimes interpret the same published version. Editing creates a new version.

The complete field-level V1 contract — indicators, conditions, entry, sizing, exits, execution, and validation layers — is specified in [canonical-strategy-schema.md](../architecture/canonical-strategy-schema.md). That document is the implementation-facing specification; this ADR records the decision. Optional multi-timeframe HTF-filter semantics are [ADR 0025](0025-multi-timeframe-htf-filter.md). The fail-closed indicator catalog is extended by [ADR 0026](0026-phase-9-single-output-indicator-catalog.md), [ADR 0027](0027-phase-9-roc-williams-cci.md), [ADR 0028](0028-phase-9-identity-constant.md), [ADR 0029](0029-phase-9-wma-momentum-mfi.md), and [ADR 0032](0032-phase-9-macd-bollinger.md).

## Consequences

- Results and live actions can point to the exact strategy version used.
- Schema migrations and compatibility policy become product responsibilities.
- The UI needs type-aware controls and human-readable summaries.
- A future node canvas can become another projection/editor rather than a new engine.
- Future custom Python strategies require a controlled adapter contract and must preserve audit/version semantics.

## Alternatives considered

- **Generate arbitrary Python from the UI:** difficult to validate, secure, migrate, and explain.
- **Node canvas first:** visually attractive but increases editor and validation complexity before core semantics are stable.
- **Separate backtest/live definitions:** rejected because semantic drift would undermine confidence and safety.
