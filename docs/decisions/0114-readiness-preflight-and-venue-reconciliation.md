# ADR 0114: Advisory readiness preflight and venue-wide reconciliation

## Status

Accepted

## Context

Operators can see a healthy local ledger, a published risk policy, and a portfolio
allocation and still not know whether those numbers fit the venue. Two recurring
gaps:

- Allocation commitments (for example eight sleeves at 40 quote, 320 total) can
  exceed a tighter account exposure cap (80) or the observed venue quote. That
  overcommitment is not the same fact as current cost-basis exposure already above the
  cap. Paper and backtest books can also assume older maker/taker rates (0.001 /
  0.002) that are cheaper than the account's reported rates (0.005 / 0.009), so
  research reads more optimistic than live fills. Portfolio daily-loss stops can
  bind tighter than the account fraction. None of this was one read-only report,
  and nothing here should silently tighten live policy.
- Local reconciliation of one book's orders is not a venue-wide comparison.
  Foreign holdings and manual venue orders are not bugs. An incomplete balance or
  open-order listing must not be treated as "the venue has nothing."

## Decision

Add two read-only operator reports. They do not place, cancel, replace, or flatten
orders, and they do not publish or tighten risk policy.

1. `GET /api/v1/operator/readiness` and `thytrader-operator readiness`
   (`--deployment-id` or `--portfolio-id`; neither is the fleet). Exact `Decimal`
   strings. Account capital follows ADR 0106: observed available quote in the
   policy quote currency, plus same-quote managed long inventory cost and working
   buy-entry reservations. Scope follows actual position/order products, not a book's
   primary product. Other quotes are disclosed and never converted. Mixed books have
   per-quote rows and null cross-quote totals. Allocation above the effective cap is
   `ALLOCATION_OVERCOMMITMENT` (advisory). Position cost plus working entry remainders
   above a cap is a violation of that cost-basis metric, not a claim about live marked
   exposure. Missing venue, managed inventory, notional, or fee evidence stays unknown.
   Paper assumptions cheaper than account evidence are
   `PAPER_FEE_ASSUMPTION_MORE_OPTIMISTIC`. Portfolio and account daily-loss stops
   are shown side by side; only live portfolios in the policy quote can compare with
   the live account daily allowance. Paper/other-quote stops are `not_comparable`.
   Deployment-scoped rows do not narrow account/portfolio inventory: siblings still
   consume caps. Inventory completeness and missing book IDs are explicit; dependent
   totals/capacities are null on incomplete reads. Unreadable runtime state means a
   null breaker latch, not false; omitted/truncated portfolio scope degrades the report.

2. `GET /api/v1/operator/venue-reconciliation` and
   `thytrader-operator venue-reconciliation`. Managed live inventory and working
   orders versus sequential provider-neutral balance and nonterminal spot-order reads.
   `EXTERNAL_INVENTORY` and `EXTERNAL_OPEN_ORDERS` are information. A shortfall or
   a managed working order absent from a complete listing is a warning; unknown is
   not rejected. Venue listing status is `complete` or `unavailable`; managed listing
   status is `complete`, `partial`, or `unavailable`. Either incomplete side nulls
   foreign/orphan/matched claims and dependent totals. Missing storage is not an empty
   fleet. Historical managed IDs (including stopped books) retain ownership; a venue
   working order locally recorded as terminal is managed status drift, not foreign.
   Pending local submits can match their durable client IDs. Duplicate balance rows
   are summed; malformed balance rows fail the whole read. Account
   identifiers and secrets are omitted. A healthy local ledger is not this report.

The Coinbase account adapter gains read-only `list_open_orders`. It pages spot order
history without status/time/source/account filters, validates every page and row,
then retains `OPEN`, `PENDING`, `QUEUED`, `CANCEL_QUEUED`, and `EDIT_QUEUED`. Only
recognized terminal statuses are omitted. Missing/non-boolean `has_next`, malformed
rows, duplicate order IDs, unknown status, cursor cycles/missing cursors, and page
exhaustion fail closed rather than returning partial rows. A queued cancel is still
unresolved working risk, never proof of cancellation. Listings are bounded,
sequential REST observations, not atomic snapshots; excessive history makes the
listing unavailable rather than truncating it silently. Completeness is within the
configured credential's visibility, not a claim about inaccessible venue accounts.
Home and Portfolio show a
`PreflightPanel`; incomplete evidence never shows Clear.

## Consequences

- Operators can see advisory overcommitment before arming more risk, without the
  report changing the published policy.
- Venue drift and foreign holdings stay visible and distinct.
- Another slice owns evidence reports and alerts; this ADR does not add them.
- Ops-contract version, global schema integration, and roadmap status remain for
  the integrating lead. Skill text and the operator JSON Schema enum name the new
  report kinds so agents can invoke them.

## Alternatives considered

- Tighten the published cap automatically when allocations exceed it. Rejected:
  the operator sets policy; a diagnostic must not change live limits.
- Treat every unmatched venue balance as an error and flatten it. Rejected:
  foreign inventory is often intentional and flattening would be a mutation.
- Reuse the existing per-book reconciliation report as venue reconciliation.
  Rejected: that report does not list the venue and must not imply a complete
  account observation.
