# 0100: Open-book unrealized PnL after paid entry fees

- Status: Accepted
- Date: 2026-10-03
- Amends: [0098](0098-library-views-book-marks-portfolio-fills.md) (fee-adjusted book reporting)
- Relates to: [0057](0057-atomic-fill-ledger-and-product-isolation.md) (applied-fill evidence)

## Context

Gross last-bar book PnL can show a gain even while paid entry fees exceed that gain. Operators
need the same fee-aware number in bot detail, portfolio sleeves, and agent reads. Cash and
equity already account for paid fees; changing them or guessing Coinbase exit fees would
mix a reporting improvement with execution policy.

## Decision

Preserve `unrealized_pnl` as signed quantity times the move from average entry price to the
last evaluated bar close. Add nullable decimal strings `entry_fees` and `unrealized_pnl_net`
to deployment positions (including compatibility `position`) and portfolio sleeve `books[]`.
Net is gross minus recorded entry fees allocated to the quantity still held. Future exit
fees are excluded; this is not a liquidation estimate.

Replay applied fills for that deployment/product since the position's entry bar, ordered
by fill time and venue fill id, through the existing average-cost ledger fold. Entries and
same-side adds accumulate fees; partial exits allocate fees proportionally to exited quantity.
Exit fees affect realized inventory, not the remaining inventory's entry fees. Require the
replayed signed quantity and average price to match the persisted position exactly.

Summary and full deployment reads load at most 1001 fills for this evidence. More than 1000,
missing fills, mismatched quantity/price, or a storage outage leaves the fee fields null while
retaining a valid gross mark. Portfolio reads use the same rules on already-loaded fills.
Without a journal mark both fee fields remain null. Legacy unprojected fills do not prove
entry costs, while recorded zero-fee fills do. Reads stay local and do not call Coinbase.

The UI prefers net and labels it `(net)`; its tooltip explains paid entry fees and excluded
future exit fees. Unknown fees retain explicitly labeled `(gross)` PnL. Agent skills describe
the same basis and null behavior. Ops contract v60 advertises `fee_adjusted_book_pnl`.
No migration is needed; Alembic remains `0059`. Existing operational cash/equity, risk checks,
fills, and aggregate ledger fields retain their existing behavior and meanings.

## Alternatives

- Estimate exit fees from a paper or Coinbase fee schedule: useful as a separate liquidation
  estimate, but future maker/taker execution and account tiers are not observed facts.
- Store remaining entry fees on positions: avoids replay, but requires migration, backfill,
  and changes to restart-sensitive fill projection for this reporting-only slice.
- Load every historical fill on summary reads: conflicts with bounded deployment reads.

## Consequences

Gross gains can correctly display as net losses. Costs remain reproducible from applied fills,
and old or very fragmented books can honestly lack a net reading. A cap breach cannot silently
report a partial fee total. Exit-fee estimation remains a separate future capability.
