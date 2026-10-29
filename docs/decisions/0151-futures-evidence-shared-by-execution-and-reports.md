# 0151: Futures evidence is shared by execution, supervision and reports

- Status: Accepted
- Date: 2026-10-29
- Amends: [0115](0115-durable-safety-alerts-and-supervision.md) (futures alert subjects),
  [0127](0127-cfm-futures-account-mirror.md) (mirror contents),
  [0129](0129-paper-futures-books-and-shared-collateral-risk.md) (funding marks, margin-rate age, collateral states),
  [0130](0130-fleet-entry-health.md) (the live CFM probe)

## Context

Several futures facts were derived one way by execution and another way by the read-only
surfaces that report on it, or were missing from them:

- The API and operator read the CFM account from the durable mirror, which never carried the
  futures open-order listing. Product exclusivity was therefore always unknown there, so
  `fleet-health` reported the live CFM scope blocked ("product exclusivity is unknown") while
  the worker, which lists venue orders itself, admitted entries.
- The fleet probe never loaded the newest settled funding rate that every live futures entry
  requires, nor ran the policy's futures caps (order contracts, gross exposure, funding rate).
  A live book could be denied on every signal while fleet health said admissible.
- Generic alert supervision read a book from an alert's subject. `FUTURES_POSITION_MISMATCH`
  is subject `deployment:<id>`, so it was resolved every cycle and reopened as a new incident.
  `FUTURES_EXTERNAL_ON_MANAGED_PRODUCT` was evaluated only for books still in scope, so it
  never resolved once the product's last book settled.
- Funding marks used the close of the book's own decision bar. A 4h or 1d bar holding the
  funding hour closes hours after the 15-minute grace, so such books were always overdue.
- The catalog margin rates' age was reported but never bounded at admission.
- The shared-collateral classifier read a flat account with a missing initial margin or
  open-order hold as idle, and accepted a mirror receipt stamped after the evaluation time.
- A futures excess reduction was keyed by the book's last fill. A definitive venue rejection
  adds no fill, so the same key was reused and the reduction was never sent again.
- On a paper futures bar that reached both the take-profit and the liquidation level, the
  resting take-profit filled first.

## Decision

- **The mirror records the futures order listing.** Each mirror cycle lists nonterminal futures
  orders through the same GET-only account client and stores only `(product_id,
  client_order_id)` pairs (`futures_account_snapshots.open_futures_orders`, Alembic 0083).
  `NULL` is unknown: the read failed (`futures_open_orders:<reason>` in `read_failures`), a row
  was not a futures product (`futures_open_orders:unclassified`), or the row predates 0083.
  Readers derive external products by the worker's exact rule: an order is managed only if a
  live book (any live futures book, retired ones included) holds the same product and client
  order id; product ids compare upper case. The mirror projection also carries the catalog's
  recorded maintenance windows, as the worker's live read does. Freshness is the receipt's. Reports
  read the books before the receipt, so an order placed in between would look external: when
  the listing holds an unmatched order, ownership is re-read once after the receipt.
- **One live entry prerequisite.** `FuturesBookState.live_entry_block()` is `entry_block()` plus
  a known settled funding rate. Execution and the fleet probe both call it, and the probe (and
  the futures-books report) load the rate the same way execution does, for perps only.
  `FUNDING_HISTORY_MISSING` is an evidence code, so a live scope blocked by it is `failed`.
- **The probe runs the gate's futures caps** once every evidence and venue check admits it, so a
  capacity denial never hides (or clears the alert for) missing evidence. Order contracts (one contract), gross exposure
  (the probe's notional, zero while flat, so only exposure already over the cap blocks) and
  the funding-rate cap, through the same function as the gate. Exposure and funding-rate
  blocks are capacity: they clear on their own and are not alerted. Futures BTC-beta and the
  per-entry leverage and liquidation buffer stay per-entry checks.
- **Alerts belong to their recorded book.** Supervision keys an alert by its `deployment_id`
  when present, else its subject's first part. A product whose every live book is settled is
  no longer managed, so its external-order alert resolves; a settled book's position-mismatch
  alert resolves too (its flat confirmation was discharged before it left managed scope).
- **Funding marks run on an hourly clock**: the close of the 1h bar ending at the funding hour,
  whatever the book's timeframe.
- **Margin-rate age bound.** `THYTRADER_FUTURES_MARGIN_RATE_MAX_AGE_SECONDS` (default 3600, 0
  turns it off) marks rates last seen longer ago as stale. Stale rates deny new futures entries
  as `FUTURES_MARGIN_UNKNOWN`. Exits, protection and liquidation keep using the last known
  rates. This extends the existing unknown-margin denial, so it is on by default. The
  futures-books report and the deployment futures view apply the same bound.
- **Collateral is idle only on known zeros.** A flat account with an unknown initial margin or
  open-order hold is `unknown`. A receipt stamped after the time the store was read is
  future-dated and `unknown`; one written after the evaluation began but before the read is
  not (ADR 0115 two-clock rule).
- **Excess reductions retry under attempt keys.** The first attempt keeps the fill-keyed
  identity; after an attempt ends rejected or cancelled with no fill, the next appends its
  number, up to three attempts. A filled attempt whose fills are not applied yet waits. After
  the third the pause and its critical alert stay for the operator.
- **Liquidation wins the paper bar.** A resting take-profit yields to a paper liquidation on
  the same candle, as it already yields to the stop.

## Consequences

- `fleet-health`, the futures-books report and the deployment futures view agree with the
  worker on exclusivity, funding and caps once the first post-0083 mirror row exists (within
  a minute of the worker starting).
- One more paged read per mirror cycle (the futures order history, the same listing the worker
  reads each live cycle). An account whose futures history outgrows the listing's page cap
  records `futures_open_orders:pagination` and reports exclusivity as unknown, as the worker
  path already would.
- A flat CFM account whose balance summary omits initial margin or the open-order hold denies
  live USD/USDC spot entries as `FUTURES_COLLATERAL_UNKNOWN` (fail-closed; the live venue has
  always reported both).
- A live book can now show `failed` fleet health for missing funding history, which pages
  `FLEET_ENTRIES_BLOCKED`; it was silently blocked before.
- Paper funding for 2h, 4h and 1d books is marked at the hourly close, which can differ from
  the previous bar-close mark.
- The paper CFM-USD fleet scope still runs no futures-specific checks; paper entries denied
  there are recorded in the decision log.
- The API and operator read books before the mirror receipt. A managed fill landing in between
  can make one report show a position mismatch until the next read; the worker's paging path
  reads the venue itself and is not affected.

## Alternatives considered

- Treat a missing listing in the reports as "not a fleet-wide block": hides a real unknown and
  leaves the reports disagreeing with the worker.
- A new policy field for the margin-rate age: a policy change re-fingerprints every policy; an
  install setting like the alert thresholds is enough.
