# 0111: Durable risk-accounting scopes

- Status: Accepted
- Date: 2026-10-06
- Supersedes in part: [0050](0050-daily-loss-drawdown-rate-collars.md), daily-loss occupancy and
  drawdown latch scope only
- Relates to: [0106](0106-account-risk-capital-and-live-startup-baselines.md),
  [0107](0107-capital-normalized-live-performance.md),
  [0064](0064-deployment-http-lifecycle-and-breaker-latch-reset.md)

## Context

Account daily loss and strategy drawdown were both read from the exposure set: running, paused,
and stopped books that still had inventory or a working entry. Stopping a flat book removed its
fills, its same-day equity change, and its `daily_loss_latched` flag from the next entry check.
A replacement book, or a late fill recorded on the stopped row, no longer counted. The same
filter treated any drawdown latch in the mode as a mode-wide kill, so one strategy blocked
unrelated books.

[ADR 0106](0106-account-risk-capital-and-live-startup-baselines.md) still defines account capital
from risk-bearing books. That capital base is not the evidence set for the day's loss. Pinned
performance capital ([ADR 0107](0107-capital-normalized-live-performance.md)) is unchanged.

## Decision

Keep exposure, open-position slots, and rolling order-rate occupancy on risk-bearing snapshots.
Do not count a stopped flat book as occupying capital or an entry-rate slot.

Daily loss and the daily-loss latch use a wider set: every retained running, paused, or stopped
deployment in that mode whose spot quote matches the proposed entry. USD, USDC, and USDT are
never added. A book whose products do not share one supported quote fails closed
(`BREAKER_MARK_MISSING`) instead of being dropped or converted.

UTC-day loss is equity minus `utc_day_open_equity` only when `utc_day_open_at` is on the
observation's UTC day. A book created that day and missing a day-open uses its recorded opening
equity, where exact zero remains valid. A flat book with an older baseline contributes realized
PnL from fills at or after UTC midnight, so a late fill counts and yesterday's loss does not.
An open book without a same-day baseline is incomplete. No midnight mark is invented.

A drawdown latch or breach applies only to books with the same `strategy_id`, or, when the
entry is discretionary, to a discretionary book on that product and quote. It does not apply to
an unrelated strategy, including one on the same product. The fraction still uses pinned
performance capital and the durable peak.

Stop does not clear either latch. `reset-breaker-latches` clears flags on that deployment only.
It does not erase the peak, performance capital, or same-day loss, and it does not resume the
bot. A loss that is still over the limit can trip again.

The worker risk snapshot and discretionary admission load this wider set. The gate re-filters
exposure and rates. No migration is added. Alembic `0065` stays reserved. Stopped live rows are
kept, so stop and replacement retain loss evidence without a new table. Paper strategy deletion
still hard-deletes paper ledgers; after that delete the gate cannot reconstruct the loss. Lead
should add `0065` only if paper deletion must retain an account-day total, and that write has
to happen before the delete.

Optional `max_order_quantity`, `max_order_notional_quote`, and `min_available_quote_reserve`
are omitted from canonical policy bytes when unset. Compiled defaults and stored documents that
omit them do not change. When set, they deny only the entry that exceeds them. Live reserve
uses observed venue available quote. Paper reserve uses paper capital minus occupied marked
exposure. Unknown live quote or a missing quantity while that cap is set fails closed.

## Consequences

- A flat stop no longer clears account daily loss or its latch for the same quote and mode.
- Unrelated strategies are no longer blocked by another book's drawdown latch.
- Operators still reset latches explicitly. Resume remains a separate command.
- Paper strategy deletion remains an evidence gap until an account-day ledger exists.
- Ops contract and `EXPECTED_SCHEMA_REVISION` are unchanged because there is no migration.
  Lead integrates operator schema text if the new reason codes should appear in findings.

## Alternatives considered

- Keep summing only risk-bearing books: rejected; that is the confirmed hole.
- Persist an account-day loss row now: rejected as unnecessary for retained live rows. Reserve
  `0065` rather than inventing a table the delete path does not write.
- Treat any same-product drawdown as shared: rejected; that still blocks unrelated strategies.
- Auto-clear latches at UTC midnight or on stop: rejected; reset stays explicit.
- Apply a quote conversion: rejected; no FX rate is evidence.
