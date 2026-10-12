# 0135: Durable live futures safety obligations

- Status: Accepted (dormant injected runtime only; not live venue approval)
- Date: 2026-10-11
- Amends: [0134](0134-live-futures-order-path.md), P2-4 persistence and safety workflow
- Relates to: [0119](0119-venue-order-observation-provenance.md),
  [0121](0121-execution-write-boundaries.md)

## Context

A diagnostic is not durable workflow state. Reconciliation replaces `mismatch_detail` on
transient faults, and margin completion can replace a flatten message. A close fill commits
separately from a later diagnostic write. After a crash or delayed fill, a stopped local-flat
book could therefore retire account reads before a post-fill venue observation. Reusing an
order intent to remember these duties would invent order authority and trigger ambiguous-submit
recovery. The P2-4 assumption that no migration was necessary does not meet the invariant.

## Decision

Alembic 0076 adds two independent typed fields to the revision-fenced deployment row:

- `futures_derisk_pending` (boolean, default false): a triggered obligation to finish de-risking,
  regardless of later margin recovery, reconciliation diagnostics, stop/resume or process loss.
- `futures_flat_confirmation_after` (nullable timezone-aware UTC timestamp): a lower bound for
  the next acceptable flat observation, **not** an observed-at or successful verification stamp.

Persist the de-risk duty before any cancellation. Persist flat verification before cancellation,
reduction submission or worker reconciliation can remove the position. Canonical fill projection
also advances this lower bound atomically with cash, fees, position and fill-application stamping,
including repair fills and product-overlay merging. Thus a late fill rearms verification even if
an earlier flat sample completed. Both fields survive ordinary runtime/scoped writes, faults,
lease changes and revision-fenced restarts. A stale writer cannot clear them.

The fill transaction arms verification for **every locally flat live futures projection**, not
only FLATTEN or margin-triggered reductions. This includes natural STOP/bracket/target fills,
signal/time exits and reductions while running, paused or stopped, and shutdown requested after
the close was already applied. Existing duties advance with subsequent fills. A partial fill
that leaves a normal matching position does not create a new flat duty or suspend its protection.
Local flatness creates an obligation, never successful venue provenance. Historical empty books
without a new fill or existing duty are not polled forever. Spot and paper remain unchanged.

Only the position supervisor clears these fields: a complete fresh signed I9 comparison must
show local and venue flat, after the durable bound and strictly after the last fill plus five
seconds, without active orders, unapplied or unprojected economics or unknown/foreign order
ownership. Cancellation may ingest a racing fill, so completion revalidates afterward. A stopped
book remains managed while either duty, unresolved intent, working order or unsettled/unprojected
inventory exists. Diagnostics are explanatory only; a de-risk completion message cannot discharge
flat verification. Entry resumption does not cancel these obligations.

Cancellation authority differs from reduction authority. The futures-specific cancellation path
requires exact persisted managed intent/order identity, fresh complete exclusive venue evidence
and equality to the full signed applied managed fill ledger. It may cancel partially filled
protection or protection accompanying proved unprojected excess. The generic cancellation guard
is unchanged. Every reduction still needs the stricter post-cancel I9/proof check and canonical
cancel-race fill ingestion. Accepted-but-unconfirmed cancellations retain their pending marker
even if the immediate confirmation read fails and through subsequent active order reads and
restarts, avoiding duplicate cancels. A malformed acknowledgement is uncertain, not accepted or
definitely rejected; terminal evidence still requires a validated observation. Ambiguous reductions retain
the original intent/client identity and are never resent.

Supervision routes proved flat/excess protection to cancellation **before** strict I9 quiescence;
otherwise a partially filled OPEN orphan would permanently block the comparison needed to remove
it. A matching open book keeps its valid resting protection. Explicit price-free reductions also
attempt independently proved cancellation before a local-flat/unprojected early return. After
cancellation, the supervisor samples I9 from the reconciled state; canonical race fills invalidate
the old sample, and repair still requires two distinct fresh valid mismatches and a strict
post-cancel ownership proof. Margin and exit callers retain their existing post-cancel reduction
guards. Neither cancellation nor a lifecycle command alone grants repair authority.

## Compatibility and rollout

The additive database defaults leave paper and spot unchanged. Migration conservatively retains
legacy injected-runtime pending markers and rechecks legacy futures flatten rows; it never
backfills successful venue provenance. Downgrade refuses while any duty remains outstanding.
Do not run an older injected futures worker against new outstanding obligations. Production
construction remains spot-only, so this does not authorize a rolling live-futures activation.

Ops contract v95 expects schema 0076. Deployment HTTP responses, operator runtime summaries and
futures-book payload models expose the two additive fields; the report schema is regenerated.
The nullable timestamp means pending verification when present, not when absent from an older
payload. Operator agents use the reports, not database edits, to inspect it. There is no manual
clear command. P2-6 remains responsible for full live-futures fleet-health/alerts.

All HTTP/CLI/service live-futures start refusals, empty `FUTURES_ORDER_PATHS`, default spot-only
composition and pending venue acceptance probes remain unchanged. Tests use synthetic venue
transport, real adapters/canonical fills and the actual fenced worker in memory and disposable
PostgreSQL. They establish workflow behavior, not authentic venue enforcement.
