# Architecture Decision Records

Architecture decision records (ADRs) capture choices that materially shape ThyTrader. They explain context and consequences so future contributors can change direction deliberately rather than accidentally.

## Accepted decisions

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-sveltekit-frontend.md) | Use SvelteKit/Svelte 5 with TypeScript for the application UI | Accepted |
| [0002](0002-modular-monolith.md) | Start as a modular monolith with separate API and worker processes | Accepted |
| [0003](0003-polyglot-storage.md) | Use PostgreSQL operationally and Parquet/Polars/DuckDB analytically | Accepted |
| [0004](0004-safe-execution-and-access.md) | Use maker-first execution, risk-first exits, and loopback-safe deployment | Accepted |
| [0005](0005-canonical-strategy-schema.md) | Use one versioned declarative strategy schema across runtimes | Accepted |

## Status values

- **Proposed:** under active consideration.
- **Accepted:** current direction.
- **Superseded:** replaced by a newer ADR; retain for history.
- **Rejected:** considered but not adopted.

## New ADR template

```markdown
# NNNN: Decision title

- Status: Proposed
- Date: YYYY-MM-DD

## Context

What forces and constraints require a decision?

## Decision

What will ThyTrader do?

## Consequences

What becomes easier, harder, required, or intentionally deferred?

## Alternatives considered

What credible alternatives were rejected, and why?
```
