# 0005: Canonical versioned strategy schema

- Status: Accepted
- Date: 2026-07-26

## Context

ThyTrader must support UI-authored strategies, templates, credible backtests, paper trading, and live execution. Separate representations for each runtime would drift and make results irreproducible. A node canvas in V1 would add complexity before the domain model is proven.

## Decision

Define one backend-validated, immutable, versioned declarative strategy schema. It represents indicators, nested conditions, sizing, execution, exits, and risk settings without embedding UI concerns.

V1 uses a structured rule builder with nested AND/OR groups. Templates produce the same schema. Backtest, paper, and live runtimes interpret the same published version. Editing creates a new version.

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
