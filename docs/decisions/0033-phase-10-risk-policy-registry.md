# 0033: Phase 10 risk-policy registry and capital allocation

- Status: Accepted
- Date: 2026-09-15
- Relates to: [0005](0005-canonical-strategy-schema.md), [0012](0012-operator-diagnostics.md),
  [0013](0013-http-first-agent-clients.md), [0019](0019-ops-contract-identity.md),
  [0030](0030-agent-e2e-primary-surface.md), [0031](0031-coinbase-first-platform-end-state.md)

## Context

Paper and live already evaluate one published long-only fingerprint per deployment, with
`max_concurrent_positions = 1` inside that document. Distinct strategy identities can already be
started concurrently, but nothing caps cross-strategy exposure or paper capital, and operator `risk`
reports `risk_policy_registry: unavailable`.

Phase 10 is the portfolio / risk-policy registry: capital allocation and multi-asset paper/live
beyond one instrument and one account-level open position. Destination still included on-demand
trades, `1m`/`2h` clocks, journals, and notify ([ADR 0031](0031-coinbase-first-platform-end-state.md));
those stayed out of this slice and later shipped
([ADR 0039](0039-on-demand-discretionary-trades.md),
[ADR 0040](0040-venue-strategy-paper-live-htf-clocks.md),
[ADR 0037](0037-phase-14-experiential-memory.md),
[ADR 0046](0046-shipped-vs-remaining-0031-destination.md)).
Intra-strategy pyramiding, multi-instrument documents, and daily-loss/drawdown remain destination.

## Decision

Ship a typed, versioned, independently testable **risk-policy registry** as
`thytrader-risk-policy-v1`. Paper and live **entry** intents must pass it before persistence. Risk-
reducing exits are not gated. Strategy documents stay single-instrument with
`max_concurrent_positions = 1`; multi-asset means concurrent deployments of those documents under
one policy.

### Policy document

| Field | Rules |
|---|---|
| `schema_version` | `thytrader-risk-policy-v1` |
| `policy_id` | Stable UUID across versions |
| `version` | Integer ≥ 1, incremented on publish |
| `quote_currency` | `USD` |
| `product_allowlist` | 0–32 unique `BASE-USD` ids; empty means no extra product restriction |
| `max_concurrent_running_deployments` | 1–32 per mode (`paper` and `live` counted separately); running and paused occupy a slot |
| `max_concurrent_open_positions` | 1–32 per mode; open, pending-entry, and pending-exit occupy a slot |
| `max_portfolio_exposure_fraction` | Plain decimal `> 0` and `≤ 1` of the mode capital base |
| `per_product_max_exposure_fraction` | Plain decimal `> 0` and `≤ 1` of the mode capital base |
| `paper_capital_quote` | Positive USD string; sum of occupied paper `paper_starting_cash` cannot exceed it |
| `allocations` | Optional unique `strategy_id` → positive `allocated_quote`; empty means any published strategy may run. Nonempty is an allowlist; paper starting cash cannot exceed that strategy's allocation; the sum of allocations cannot exceed `paper_capital_quote` |

Canonical compact sorted JSON and `sha256:<hex>` identity cover the whole document.

### Compiled default

When no published row is active, execution and operator reports use a compiled default: empty
allowlist and allocations, eight running slots and eight open positions per mode, unit exposure
fractions, and `paper_capital_quote` `100000`. That default is the **shipped conservative envelope
that already permits multi-asset paper/live**; operators may tighten or name allocations by
publishing a version.

### Capital base

- Paper: `paper_capital_quote`.
- Live: remaining quote cash plus marked open/pending exposure on occupied live deployments.

### Surfaces

- `GET` / `PUT /api/v1/risk-policy` — read the effective policy; publish a new immutable version.
  `PUT` requires durable PostgreSQL storage.
- `thytrader-runtime show-risk-policy` (read-only) and `set-risk-policy --confirm` (mutation).
  Live acknowledgement is not required; this does not arm live trading.
- Operator `risk` reports `risk_policy_registry: available` plus fingerprint, source
  (`compiled_default` or `published`), counts, allowlist, and pause/mismatch findings. It omits
  account balances.
- Deploy create and closed-bar entries evaluate the same gate. Denying an entry skips the bar; it
  does not pause or persist an intent.

Alembic `0021` stores published versions and a single active pointer. Ops contract becomes
`thytrader-ops-contract-v7` with `expected_schema_revision` `0021`.

## Consequences

- Two published single-instrument strategies (for example BTC-USD and ETH-USD) can run paper or live
  together when the policy has spare slots, capital, and allowlist room.
- One strategy identity still has at most one running deployment per mode.
- Intra-strategy pyramiding, multi-instrument strategy documents, daily-loss / drawdown circuit
  breakers, order-rate limits, and on-demand orders remain out of this slice.
- Backtest kernels stay single-position; they do not consume the runtime registry.
- 5m live, HTF paper/live, `1m`/`2h` clocks, journals, and notify remain later phases.

## Alternatives considered

- **Widen `max_concurrent_positions` on the strategy document:** rejected for this slice; that would
  change publication fingerprints and the bar-backtest kernel. Portfolio concurrency lives on the
  registry.
- **Multi-instrument strategy schema:** deferred; concurrent single-instrument deployments are the
  multi-asset paper/live path here.
- **Keep operator `unavailable` until every security-and-risk.md control exists:** rejected; the
  named Phase 10 gap is the missing registry, not the full destination policy catalog.
- **Gate exits through the same caps:** rejected; capital protection outranks the entry budget.
