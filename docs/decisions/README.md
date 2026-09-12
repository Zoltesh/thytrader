# Architecture Decision Records

Architecture decision records (ADRs) capture choices that materially shape ThyTrader. They explain context and consequences so future contributors can change direction deliberately rather than accidentally.

## Accepted decisions

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-sveltekit-frontend.md) | Use SvelteKit/Svelte 5 with TypeScript for the application UI | Accepted |
| [0002](0002-modular-monolith.md) | Start as a modular monolith with separate API and worker processes | Accepted |
| [0003](0003-polyglot-storage.md) | Use PostgreSQL operationally and Parquet/Polars/DuckDB analytically | Accepted |
| [0004](0004-safe-execution-and-access.md) | Use maker-first execution, risk-first exits, loopback-safe deployment, and restrictive credential permissions | Superseded in part by 0006 |
| [0005](0005-canonical-strategy-schema.md) | Use one versioned declarative strategy schema across runtimes | Accepted |
| [0006](0006-credential-permission-acceptance.md) | Accept operator-selected Coinbase keys with additional permissions | Accepted |
| [0007](0007-immutable-research-run-specifications.md) | Publish immutable research-run specifications before simulation | Accepted |
| [0008](0008-deterministic-signal-evaluation.md) | Version deterministic signal evaluation separately from request-only runs | Accepted |
| [0009](0009-deterministic-bar-level-backtest-engine.md) | Version bar-level backtest simulation separately from signal evaluation | Accepted |
| [0010](0010-constant-spread-backtest-provenance.md) | Version constant-spread stress assumptions as immutable backtest evidence | Accepted |
| [0011](0011-derived-buy-and-hold-benchmark.md) | Keep buy-and-hold comparison as a derived backtest report | Accepted |
| [0012](0012-operator-diagnostics.md) | Versioned operator diagnostics CLI/API and confirmation-gated research CLI | Accepted |
| [0013](0013-http-first-agent-clients.md) | HTTP-first agent CLIs and confirmation-gated runtime control skill | Accepted |
| [0014](0014-watchlist-and-5m-research.md) | Watchlist ingest and 5m research datasets; paper/live stay 1h | Accepted — superseded in part by 0015, 0016, and 0018 |
| [0015](0015-worker-owned-ingest-and-ops-workspace.md) | Worker-owned ingest jobs, inclusive Coinbase paging, heartbeats, ops workspace | Accepted |
| [0016](0016-longer-complete-5m-datasets.md) | Longer complete 5m datasets via a 25,920-bar cap and chunked UTC-day publish | Accepted |
| [0017](0017-maker-limit-bar-backtest.md) | Maker-limit bar backtest as `thytrader-bar-backtest-v3` | Accepted |
| [0018](0018-5m-paper-not-live.md) | Paper may evaluate closed 5m bars; live remains 1h | Accepted |
| [0019](0019-ops-contract-identity.md) | Health/CLI ops contract independent of package version `0.1.0` | Accepted |

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
