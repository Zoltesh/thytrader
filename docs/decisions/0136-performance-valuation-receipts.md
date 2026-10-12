# 0136: Fail-closed performance valuation receipts

- Status: Accepted
- Date: 2026-10-12
- Relates to: [0098](0098-library-views-book-marks-portfolio-fills.md),
  [0107](0107-capital-normalized-live-performance.md),
  [0132](0132-execution-cycle-shared-reads-and-budget.md)

## Context

The performance report reobserves a preview close, while deployment detail and portfolio
positions intentionally use journal-local marks. Without a receipt, different observations
of a mutable candle tail looked like an arithmetic discrepancy. Performance also accepted
preview candles even when their quality report explicitly classified them as stale.

## Decision

Keep the two valuation sources separate. No detail/portfolio reads gain market-data calls,
and no ledger arithmetic, risk policy or accounting qualification changes.

Performance excludes `quality.is_stale` previews from its accepted marks. The quality rule
remains strictly greater than two intervals since candle close. Missing or stale required
marks null current PnL, return, drawdown and exposure, with degraded `MISSING_MARK`; unresolved
accounting retains its existing precedence. Recorded realized PnL, closed-trade counts and
paid fees remain available as population evidence, never a complete-account claim.

Each performance book has a `valuation` receipt: exact candidate price, UTC candle-close
and preview observation times, source `market_data_preview`, freshness/availability state,
reason code and explicit `used`. Only fresh evidence actually used for that book's inventory
has `used: true`. Stale prices remain explanatory candidates, never accepted marks. Failed
reads manufacture neither a price nor an observation time. Flat/trade-only books remain
visible with `not_required`, no preview read and no price/timestamps.

CLI text and browser performance cards name preview valuation and distinguish accepted
marks from rejected candidates. Ops contract v97 advertises `performance_valuation_receipts`;
the v1 operator report schema gains additive receipt/recorded-statistics fields. No migration.

Separately, execution-worker feed/reload wake requests are consumed at cycle start before
any await. Requests arriving during work coalesce into one immediate sequential successor.
With no new nudge, poll-after-work cadence is unchanged; cancellation awaits idle-waiter
cleanup. This does not introduce concurrent book processing or alter trading decisions.

## Alternatives considered

- Unify onto journal marks: hides the newly observed preview the performance report promises.
- Reobserve prices on detail/portfolio reads: violates ADR 0098's journal-local contract.
- Label stale current PnL without excluding its mark: still promotes rejected evidence into
  current economics. Retain only the candidate receipt and known historical statistics.
- Queue every wake or run concurrent cycles: redundant work and altered execution ordering.
