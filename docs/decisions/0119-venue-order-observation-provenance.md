# ADR 0119: Persist venue order-state observation separately from local writes

- Status: Accepted
- Date: 2026-10-06
- Extends: ADR 0058 and quantitative protection evidence in ADR 0112

## Context

`Order.updated_at` changes during local accounting as well as REST reconciliation. Using that
field as a venue verification time can make an old order look freshly observed after a local
write. A null or stale venue timestamp must not become a green current-evidence claim.

## Decision

Add nullable `Order.venue_observed_at` and the corresponding PostgreSQL column (migration
0068). Successful identified live broker order-state reads stamp receipt time during ordinary
reconciliation and attached-child import. UNKNOWN results clear this evidence. Paper and
ordinary local writes do not create or refresh it. It survives process restart. Existing rows
are left NULL until an actual observation: historical timestamps are never inferred from
`created_at` or `updated_at`.

This observation proves the returned order state at that read, not a separate audit of venue
geometry, all account activity, or guaranteed stop execution. Protection reporting must keep
submitted geometry/quantity evidence and observation freshness distinct and disclose gaps.
The change creates no orders, cancellations, bot resumes or policy changes.

## Alternatives

- Reuse `updated_at`: rejected because local accounting writes would invent freshness.
- Backfill all legacy rows with migration time: rejected because migration is not venue proof.
- Always omit timing evidence: truthful but unnecessarily prevents current evidence after a
  successful reconciliation. Durable observation provenance gives an auditable bounded claim.

## Verification

Hermetic tests cover live/paper separation, unknown-result invalidation, child-specific reads,
local-write preservation, PostgreSQL restart, and nullable migration round trips. Release
integration must rechain migration 0068 after the reserved alert/fleet migrations and advertise
the actual head through the shared ops contract before deployment.
