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
paid fees remain available as population evidence, never a complete-account claim. At the
reporting boundary, fold recorded fills independently for **every product**, then sum
trade counts, realized PnL and fees. A ledger fill lacks product identity; cross-product
fills must never be folded as one fungible inventory lot. The existing current PnL,
return, drawdown, exposure and open-book ledger projections remain unchanged.

Each book exposes distinct recorded `realized_net_pnl` and `total_fees`. Existing open-book
`total_net_pnl` projections share deployment cash and **MUST NOT be summed**. A historical
row's `total_net_pnl` is null when inventory is open, so it does not duplicate historical
profit already embedded in that shared cash. Complete all-flat historical rows retain
realized totals only when their exact sum equals deployment total PnL. Otherwise all
historical totals are null and a warning explains that non-fill cash, such as applied
funding, prevents a proven partition. Recorded realized PnL is fill-only; it excludes
non-fill cash flows. Paid fees are already included in net PnL.

Each performance book has a `valuation` receipt: exact candidate price, UTC candle-close
and preview observation times, source `market_data_preview`, freshness/availability state,
reason code and explicit `used`. Only fresh evidence actually used for that book's inventory
has `used: true`. Stale prices remain explanatory candidates, never accepted marks. Failed
reads manufacture neither a price nor an observation time. Flat/trade-only books remain
visible with `not_required`, no preview read and no price/timestamps.

CLI text names the preview channel without claiming a fresh observation and prints
aggregate and per-book recorded facts. Browser performance cards distinguish accepted
marks from rejected candidates, showing unknown current PnL/return/exposure separately
from known closed trades, realized PnL and paid fees. Unknown recorded fields stay unknown,
not zero. When the performance request fails, the card labels its full-detail ledger
fallback journal-local rather than preview valuation.

Ops contract v97 advertises `performance_valuation_receipts`;
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
