# 0112: Quantitative protection evidence

- Status: Accepted
- Date: 2026-10-06
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

Live `protection_status: covered` requires sufficient recent persisted OPEN stop evidence that:

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
statuses: a newer canceled/filled/rejected/unknown observation never resurrects an older OPEN
row. Latest aware timestamps win; equal-time contradictory status/geometry or missing timestamp
ordering is unverified. Duplicate partial rows never sum; tied matching rows use the smaller
remainder.

Local order timestamps are **not** a guaranteed successful venue reconciliation instant.
`observed_at` is the latest inspected persisted row update; `observation_source` says
`persisted_order`, `synthetic_worker`, or `none`. `verified_at` is always null in this slice:
there is no dedicated persisted venue-verification timestamp in the existing model. The report
never manufactures one. `evaluated_at` is the actual reporting clock. `freshness` is
`recent_local`, `stale`, or `unknown`, strictly describing local-row recency, never venue
freshness. A contributing OPEN row must be aware, not future-dated, and no more than 120 seconds
old (`freshness_max_age_seconds`). This reporting bound is four default 30-second worker polls,
independent of strategy candle frequency. Custom slow polling may show unverified protection;
it does not change supervision, pause choices, or broker behavior. Stale, undated, future-dated,
or unidentified OPEN stops do not add covered quantity.

`covered` therefore remains a qualified **persisted-state** claim for matching recent ETH/ADA
brackets, not a live venue guarantee. `local_observation_only` and null `verified_at` prevent a
green venue badge. Missing evidence on a legacy payload is also unverified in the UI. Side and
geometry validity are independent of confirmation/quantity: a pending matching stop can have
valid geometry but zero covered quantity.

Paper books remain `covered` and `open_protected`. Evidence `mechanism` is `synthetic`,
`worker_dependent` is true, `venue_resting` is false, and observed/verified times are null.
Null times mean unknown. Nothing invents a price, fee, or timestamp.

`protection` is added to deployment `positions[]`, operator runtime `books[]`, and portfolio
sleeve `books[]`. Legacy `protection_status` and `position_state` remain. The UI must not paint
a worker-dependent, partial, take-profit-only, unconfirmed, stale, or locally-observed-only
stop as a green venue badge.
Operator books still omit prices, cash, and order payloads; coverage quantities are the
protection evidence, not an inventory dump.

Ops contract and the generated operator JSON schema are unchanged in this slice. Lead integrates
those after the other slices merge.

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
