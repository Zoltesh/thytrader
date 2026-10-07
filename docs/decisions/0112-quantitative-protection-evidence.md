# 0112: Quantitative protection evidence

- Status: Accepted
- Date: 2026-10-06
- Extended by: [0119](0119-venue-order-observation-provenance.md) (real order-state receipt provenance)
- Amends: [0058](0058-protection-lifecycle-accounting.md) (a resting exit is not cover unless it is
  a confirmed stop), [0097](0097-runtime-parity-and-observability.md) (open_protected follows the
  stricter status), and [0098](0098-library-views-book-marks-portfolio-fills.md) (a live
  closing-side order on a bounded read is not cover by itself; paper covered stays, but is not
  venue-resting)

## Context

`book_protection_status` treated any OPEN or PENDING protective order as covered. It did not
require the closing side, remaining quantity, or stop geometry. A take-profit-only closing order
counted. The attached-child shortcut returned covered before those checks. Pending and unknown
orders could read as confirmed. Paper books correctly stayed covered, but nothing distinguished
the worker's synthetic stop from a venue-resting order, so the UI could show a false green.

Operators need to see how much of a book is actually stopped, whether that stop matches the
working geometry, and whether the observation is confirmed.

## Decision

Classification stays in `execution/protection.py`. It does not submit, cancel, or replace orders.

Live `protection_status: covered` requires sufficient freshly venue-observed OPEN stop evidence that:

- has a venue identity (directly or through an attached-child link), and is on the closing side;
- has remaining quantity (original minus filled, floored at zero), with unique orders summed and
  the same venue child counted once;
- has an executable venue order kind (`trigger_bracket` or `stop_limit`), not merely a STOP
  purpose or stray trigger on a plain limit/marketable order;
- has a positive stop trigger equal to the book's working stop, ordered against its working
  target when present. Entry is not a geometry anchor: a profitable trailing long stop may
  exceed entry (and a short stop may fall below entry);
- matches the book's target when the order is a trigger bracket (explicit take-profit, or the
  bracket limit price when take-profit is unset, which is how a post-fill OCO is stored).

A stop-limit with no take-profit still covers a book whose stop matches (ADR 0090), provided
its positive limit is at or through the trigger on the closing side (sell limit <= stop, buy
limit >= stop). Its `geometry_basis` is `stop_limit_trigger` without a target, otherwise
`working_target`. This is executable order geometry, **not** current venue-price or fill
certainty. Missing geometry is unknown, not invented from entry or a historical trail extreme.
A take-profit alone does not. A pending or unknown stop does not add confirmed quantity; if it is
the only candidate, status is `unknown`. A partial stop is `unprotected` with the shortfall in
evidence, unless an unconfirmed candidate remains, in which case status is `unknown` and covered
quantity is only the confirmed remainder. Stale brackets whose stop or target do not match the
current book do not count. Identity folding includes terminal rows before filtering active
statuses. Precedence uses actual `venue_observed_at`, never local `updated_at`: a local rewrite
cannot resurrect older OPEN evidence over terminal or UNKNOWN evidence. Duplicate histories
with UNKNOWN/missing receipt provenance, conflicting submitted geometry/original quantities,
equal-receipt status conflicts, terminal-to-active transitions, or regressing partial fills stay
unverified. A status read cannot resolve a submitted-geometry conflict. Matching histories fold
in venue-receipt order; duplicates never sum, and tied matching rows use the smaller remainder.

[ADR 0119](0119-venue-order-observation-provenance.md) adds the real order-state receipt timestamp.
`observed_at` is the latest relevant stop's venue receipt, never a local row update.
`verified_at` is the **oldest receipt among contributing fresh OPEN stops**; for partial cover it
verifies only that fraction, not the whole book. Both are null if that evidence is unavailable.
`observation_source` is `venue_order_state` for actual receipts, `persisted_order` for legacy/local
unverified evidence, `synthetic_worker` for paper, or `none`. `freshness` is `recent_venue`,
`stale`, or `unknown`, assessed against the reporting clock `evaluated_at`. Each contributing
stop needs its own aware, non-future receipt no more than 120 seconds old
(`freshness_max_age_seconds`, four default 30-second worker polls, independent of candle clock).
The newest partial stop cannot refresh an older one. Mixed stale/unknown matching candidates
are disclosed conservatively; only fresh quantity contributes. Local writes never renew this
bound. Custom slower polling can show unverified protection without changing operation.
UNKNOWN reads invalidate old receipt evidence; legacy rows remain unknown until actually read.

`covered` is a qualified **order-state plus persisted submitted geometry** claim, not an
independent venue-geometry audit, whole-account reconciliation, current market mark, or fill
guarantee. `geometry_basis` always names the persisted geometry check. The UI labels sufficient
fresh state evidence **Order state fresh** in amber, with **venue geometry not independently
verified**, never a blanket green audit claim. Legacy/local-only, stale, and missing evidence
remain **Unverified**. Side and geometry validity are independent of confirmation/quantity:
a pending matching stop can have valid persisted geometry but zero covered quantity.

Paper books remain `covered` and `open_protected`. Evidence `mechanism` is `synthetic`,
`worker_dependent` is true, `venue_resting` is false, and observed/verified times are null.
Null times mean unknown. Nothing invents a price, fee, or timestamp.

`protection` is added to deployment `positions[]`, operator runtime `books[]`, and portfolio
sleeve `books[]`. Legacy `protection_status` and `position_state` remain. The UI must not paint
a worker-dependent, partial, take-profit-only, unconfirmed, stale, or locally-observed-only
stop as a green venue badge.
Operator books still omit prices, cash, and order payloads; coverage quantities are the
protection evidence, not an inventory dump.

Ops contract and the generated global operator JSON schema remain lead-owned. This narrow
provenance follow-up changes the evidence enums to `venue_order_state` / `recent_venue` and adds
`venue_evidence_stale`; lead must regenerate the integrated schema before release.

## Consequences

- Readers that treated any closing-side OPEN or PENDING order as cover now see `unprotected` or
  `unknown`. Matching live ETH/ADA full brackets and matching stop-limits stay covered.
- A paper book still reads `covered`, but badges say worker stop rather than venue TP/SL.
- Execution, risk, and broker submission are unchanged. `attached_entry_covers` still decides
  whether the worker rests another bracket; the report no longer treats that helper as proof.

## Alternatives considered

- Changing `attached_entry_covers` so the worker also uses remaining quantity. Rejected for this
  slice: that would change when a replacement bracket is submitted.
- A new `protection_status` value for paper. Rejected: ADR 0098 readers keep `covered`, and the
  evidence carries the distinction.
- Omitting coverage quantities from operator books to preserve the old redaction. Rejected:
  partial cover is not visible without them. Prices and cash stay omitted.
