# Architecture Decision Records

Architecture decision records (ADRs) capture choices that materially shape ThyTrader. They explain context and consequences so future contributors can change direction deliberately rather than accidentally.

## Accepted decisions

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-sveltekit-frontend.md) | Use SvelteKit/Svelte 5 with TypeScript for the application UI | Accepted |
| [0002](0002-modular-monolith.md) | Start as a modular monolith with separate API and worker processes | Accepted |
| [0003](0003-polyglot-storage.md) | Use PostgreSQL operationally and Parquet/Polars/DuckDB analytically | Accepted |
| [0004](0004-safe-execution-and-access.md) | Use maker-first execution, risk-first exits, loopback-safe deployment, and restrictive credential permissions | Superseded in part by 0006 |
| [0005](0005-canonical-strategy-schema.md) | Use one versioned declarative strategy schema across runtimes | Accepted — extended by 0025, 0026, 0027, 0028, 0029, and 0032 |
| [0006](0006-credential-permission-acceptance.md) | Accept operator-selected Coinbase keys with additional permissions | Accepted |
| [0007](0007-immutable-research-run-specifications.md) | Publish immutable research-run specifications before simulation | Accepted |
| [0008](0008-deterministic-signal-evaluation.md) | Version deterministic signal evaluation separately from request-only runs | Accepted — extended by 0026, 0027, 0028, 0029, and 0032 |
| [0009](0009-deterministic-bar-level-backtest-engine.md) | Version bar-level backtest simulation separately from signal evaluation | Accepted |
| [0010](0010-constant-spread-backtest-provenance.md) | Version constant-spread stress assumptions as immutable backtest evidence | Accepted |
| [0011](0011-derived-buy-and-hold-benchmark.md) | Keep buy-and-hold comparison as a derived backtest report | Accepted |
| [0012](0012-operator-diagnostics.md) | Versioned operator diagnostics CLI/API and confirmation-gated research CLI | Accepted |
| [0013](0013-http-first-agent-clients.md) | HTTP-first agent CLIs and confirmation-gated runtime control skill | Accepted |
| [0014](0014-watchlist-and-5m-research.md) | Watchlist ingest and 5m research datasets; paper/live stay 1h | Accepted — superseded in part by 0015, 0016, and 0018 |
| [0015](0015-worker-owned-ingest-and-ops-workspace.md) | Worker-owned ingest jobs, inclusive Coinbase paging, heartbeats, ops workspace | Accepted |
| [0016](0016-longer-complete-5m-datasets.md) | Longer complete 5m datasets via a 25,920-bar cap and chunked UTC-day publish | Accepted |
| [0017](0017-maker-limit-bar-backtest.md) | Maker-limit bar backtest as `thytrader-bar-backtest-v3` | Accepted |
| [0018](0018-5m-paper-not-live.md) | Paper may evaluate closed 5m bars; live remains 1h | Accepted — superseded in part by 0036 |
| [0019](0019-ops-contract-identity.md) | Health/CLI ops contract independent of package version `0.1.0` | Accepted |
| [0020](0020-complete-only-15m-datasets.md) | Complete-only 15m historical datasets; strategy/paper/live clocks stay 1h/5m | Accepted |
| [0021](0021-complete-only-30m-datasets.md) | Complete-only 30m historical datasets; strategy/paper/live clocks stay 1h/5m | Accepted |
| [0022](0022-complete-only-6h-datasets.md) | Complete-only 6h historical datasets; strategy/paper/live clocks stay 1h/5m | Accepted |
| [0023](0023-complete-only-1d-datasets.md) | Complete-only 1d historical datasets; strategy/paper/live clocks stay 1h/5m | Accepted |
| [0024](0024-agent-data-loop-completeness-and-image-identity.md) | Fail-closed watch completeness and stale-image identity for every agent CLI | Accepted |
| [0025](0025-multi-timeframe-htf-filter.md) | Optional HTF filter + LTF entry; research-only closed-bar alignment | Accepted |
| [0026](0026-phase-9-single-output-indicator-catalog.md) | Phase 9 first slice: `highest`, `lowest`, and population `stdev` | Accepted |
| [0027](0027-phase-9-roc-williams-cci.md) | Phase 9 second slice: `roc`, `williams_r`, and `cci` | Accepted |
| [0028](0028-phase-9-identity-constant.md) | Phase 9 third slice: `identity` OHLCV and `constant` levels | Accepted |
| [0029](0029-phase-9-wma-momentum-mfi.md) | Phase 9 fourth slice: `wma`, `momentum`, and `mfi` | Accepted |
| [0030](0030-agent-e2e-primary-surface.md) | Agent-driven E2E is the primary product surface; UI still required | Accepted |
| [0031](0031-coinbase-first-platform-end-state.md) | Coinbase-first platform end-state: on-demand trades, venue TFs, single- and multi-asset | Accepted |
| [0032](0032-phase-9-macd-bollinger.md) | Phase 9 fifth slice: `macd` and `bollinger` with referenceable series ids | Accepted |
| [0033](0033-phase-10-risk-policy-registry.md) | Phase 10 risk-policy registry, capital allocation, concurrent single-instrument paper/live | Accepted |
| [0034](0034-phase-12-agent-orchestration-yolo.md) | Phase 12 playbook over existing CLIs and default-off YOLO confirmation opt-in | Accepted |
| [0035](0035-phase-11-research-rigor.md) | Phase 11 walk-forward, OOS, and cross-market research studies; richer templates; V1/V2/V3 matrix | Accepted |
| [0036](0036-phase-13-live-extras.md) | Phase 13 5m live, ATR trailing stops, user-order WS, native OCO brackets | Accepted |
| [0037](0037-phase-14-experiential-memory.md) | Phase 14 journals, sentiment/pattern hooks, monitor, and config-gated notify | Accepted |
| [0038](0038-complete-only-1m-2h-4h-datasets.md) | Complete-only 1m, 2h, and 4h historical datasets; strategy/paper/live clocks stay 1h/5m | Accepted |

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
