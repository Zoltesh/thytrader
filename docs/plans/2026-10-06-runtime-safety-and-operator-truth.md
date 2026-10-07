# Runtime safety and operator truth — 2026-10-06

Status: implementation and verification complete at `ab8527b`; release pending operator
authorization; not a shipped claim. The final
[release acceptance checklist](2026-10-06-release-acceptance.md) freezes the remaining scope
and requires evidence at the actual concurrency, protection and reporting boundaries.

## Authorization and boundaries

The user requested implementation of the read-only review's verified fixes and recommended
improvements, a branch/PR merged to main, and a healthy running fleet afterward. This is a
contributor change. The live deployment identities, strategy snapshots, configured risk limits,
credentials, twin history and deliberate pause choices are not to be replaced or loosened.
No test is to use production exchange credentials or operational PostgreSQL. Notifications
require an operator-configured destination; no external endpoint will be invented.

## Work streams

| Slice | Acceptance criteria |
| --- | --- |
| Lifecycle and reconciliation | Preexisting pause does not truncate order reconciliation; missing candles cannot remove protection without an exit; stopped discretionary and secondary-product inventory is supervised; cancel/fill races remain reconcilable. |
| Durable loss scope | Stop/replace cannot erase today's realized account loss or reset a daily latch; drawdown latches are strategy-scoped; quote currencies, UTC rollover and explicit resets remain correct. |
| Protection evidence | Confirmed coverage requires correct closing side, remaining quantity and stop geometry; pending/unverified/TP-only evidence is not green; synthetic protection and verification freshness are disclosed. |
| Clock/data longevity | Shared-clock warmup and reference rollover use sufficient causal coverage; long-lived execution avoids oversized lifetime provider requests without silently reseeding indicators or hiding real gaps. |
| Readiness and venue reconciliation | Read-only allocation/capacity/fee comparison and scoped venue inventory/order reconciliation expose partial evidence, foreign holdings and tight effective limits honestly. |
| Alerts and supervision | Durable deduplicated safety incidents, recoveries and optional delivery; clock-aware missed evaluation/maintenance and consecutive failures; exits remain independent of entry inhibition. |
| Execution/research evidence | Exact recorded live costs and attributed twin differences; bounded reproducible per-bar research explanations, no invented fills or fees. |
| Inventory and controls | Complete/paged deployment inventory and explicit summary/full ledgers; confirmation-gated, restart-safe mode-wide entry disarm, separate managed stop/flatten and explicit rearm. |

Each slice needs typed domain contracts, focused regression tests, a decision record, operator skill
discovery and a usable API/CLI/UI where applicable. Shared ops-contract and report-schema changes
are integrated together, not published piecemeal to the live stack.

## Validation and release gates

1. Baseline current bot IDs/statuses, open books/protection, health, risk and reconciliation.
2. Verify GitNexus impact and source-level invariants before edits. An UNKNOWN graph result is
   unresolved and requires source/caller verification; a HIGH result requires explicit review.
3. Run focused tests per slice, then integrated Python tests (including isolated PostgreSQL),
   Ruff, formatter, type checker, frontend lint/type/unit/browser tests and production build.
4. Reindex and inspect integrated graph changes, migrations, routes, schema/skill contracts and
   complete diff. Review safety fixes independently before deployment.
5. Back up operational PostgreSQL without exposing credentials. Build before the controlled
   migration/service replacement; preserve volumes and stored deployment identities.
6. Merge reviewed code to main, switch the local checkout to updated main, deploy that revision,
   and verify readiness/schema plus the same intended running bots, venue protection and
   reconciliation. A configured pause is not an instruction to resume it.
7. Report actual delivered capabilities, test results and limitations. Never equate local ledger
   reconciliation with a complete independent venue account audit, or claim guaranteed stop
   execution through a gap.
