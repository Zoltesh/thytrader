# 0108: Account-read failures and audit recovery evidence

- Status: Accepted
- Date: 2026-10-04
- Relates to: [0030](0030-agent-e2e-primary-surface.md),
  [0061](0061-application-trust-boundary.md)

## Context

Intermittent account-read failures were reported only as `EXCHANGE_UNAVAILABLE`, even
though portfolio reads perform balances, permissions, and valuation requests.
Reconciliation compressed historical failures into one count, obscuring recovered
WebSocket incidents and making supported diagnosis require a separate audit lookup.

## Decision

The Coinbase read-only account adapter translates transport errors into provider-neutral,
typed operation/category/status evidence. Operator health and exchange reports expose
that safe evidence. Raw provider bodies, URLs and exception messages stay private.
Unknown errors continue to fail closed; missing evidence is not a successful read.

Account GETs retry once after 0.5 seconds on timeout/network or HTTP 502/503/504,
with fresh request signing and the same pagination cursor. Exhaustion returns no
partial balances and exposes the attempt count. Authentication, 429 rate limits,
malformed responses and pagination errors do not retry. Order writes never use
this helper, so ambiguous submission reconciliation is unchanged.

Reconciliation retains one finding per failure from the latest 20 audit events, with
event identity, UTC time, action, provider/product, and explicit recovery evidence.
Only documented user/market WebSocket failure-to-connected action pairs establish a
recovery, strictly later and on the same category/provider/product. Unknown action
types have unknown recovery. A connection event never resolves an order outcome.

Recovered failures stay visible and degraded while in this bounded window. Current
feed health is independently reported by runtime diagnostics. No historical audit
records are deleted, rewritten or automatically waived. No trading rules, arming,
order submission, or risk gates change.

Ops contract v65 advertises `exchange_read_failures` and `audit_failure_evidence`.
The report envelope remains v1 and Alembic remains 0061; additions are nullable fields.

## Consequences

An agent can identify the failed account operation and inspect audit recovery through
the shipped operator skill. Boundaries continue to withhold sensitive raw detail.
Recovery is limited to evidence in the inspected window and known action pairs;
unrelated successes do not resolve failures.

## Alternatives considered

- Raw exception strings and audit details: rejected because they may expose credentials,
  account identifiers or balances.
- Erasing recovered failures or treating later generic success as recovery: rejected
  because it conceals history and can waive unresolved order failures.
- A new unbounded audit endpoint: unnecessary; the existing bounded read supports this slice.

- Unbounded account retries or retries of all HTTP failures: rejected; authentication
  and rate-limit failures require explicit diagnosis, and operational reads must remain bounded.
