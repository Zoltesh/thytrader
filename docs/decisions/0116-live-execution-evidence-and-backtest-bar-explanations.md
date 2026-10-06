# 0116: Live execution evidence and backtest bar explanations

- Status: Accepted
- Date: 2026-10-05
- Amends: [0057](0057-atomic-fill-ledger-and-product-isolation.md) (read-only use of recorded fills),
  [0087](0087-per-bar-decision-timeline.md) (journaled closes as slippage reference),
  [0090](0090-research-correctness-optional-take-profit-diagnostics.md) (verified signal traces),
  [0102](0102-explicit-paper-live-twin-links.md) (explicit pairs only)

## Context

Operators can see ledger net PnL and backtest cost attribution, but not a closed-trade
split of recorded fill-price PnL, exact entry and exit fees, and slippage against the
journaled decision close. Missing venue fees or liquidity must not be shown as zero.
Paper/live twins need the same honesty: an incomplete journal, a disjoint fill window, or
unrecorded liquidity is a cannot-compare, not a smoothed comparison. Fee-normalized
comparisons must stay counterfactual.

Backtest signal traces already explain entry and exit-rule outcomes, but they do not show
which simulated fills the immutable result recorded on those bars. An unbounded stored
trace of every execution event would duplicate the result and invite lookahead.

## Decision

1. **Recorded execution quality.** `thytrader-execution-quality-v1` folds one deployment's
   applied fills into closed round trips. Each trip reports fill-price PnL before fees,
   exact recorded entry and exit fees, and net PnL as their difference. Slippage is the
   signed distance from the journaled decision close of the bar that contains the fill,
   and only when that close exists. Maker/taker is reported only for post-only and
   marketable orders. Unapplied fills, orphan fills, unknown liquidity, missing closes,
   open cycles, position mismatches, and ledger fee-allocation deltas are evidence
   reasons. They are never defaulted to zero and never written back onto fills.

2. **Explicit twin comparison.** `thytrader-execution-twin-comparison-v1` compares only a
   saved paper/live link. The pair is comparable only when both reports are complete and
   their recorded fill windows overlap. Otherwise the response says cannot-compare and
   lists the reasons. Fee normalization re-prices live fills that have recorded liquidity
   at the paper book's stored or documented fee assumptions. It is a separate
   counterfactual. Observed live fees and realized net PnL are not rewritten.

3. **Bounded bar explanations.** `thytrader-backtest-bar-explanation-v1` reuses the
   fingerprint-checked signal-trace re-evaluation and joins the immutable result's trades
   and equity marks. Pages are bounded (default 100, maximum 500), oldest bar first, and
   carry result, run, strategy, dataset, and trace fingerprints. The evaluation-end
   liquidation bar is listed as outside the trace because indicators never see it. No new
   trace is persisted and the simulator is not changed.

4. **Surfaces.** Read-only HTTP:

   - `GET /api/v1/deployments/{id}/execution-quality`
   - `GET /api/v1/deployments/{id}/execution-quality/twin`
   - `GET /api/v1/backtests/{result_fingerprint}/bar-explanations`

   `thytrader-operator execution-quality` and `thytrader-research explain-bars` are
   HTTP-only reads. Research mutations still require `--confirm`. This ADR does not bump
   the ops contract or rewrite operator report schemas; that integration belongs to the
   release lead.

## Consequences

- Operators can separate price PnL, recorded fees, and unknown slippage without scraping
  fills or inventing venue liquidity.
- A twin comparison that lacks journaled closes or an overlapping window is explicitly
  not comparable.
- Bar explanations stay reproducible from the published result and its verified trace.
- Decision-journal paging is bounded. A long book can report `decision_coverage_limited`
  instead of reading the journal without a cap.
- Historical fills are not migrated or corrected by this slice.

## Alternatives considered

- **Infer missing live fees as zero or as the paper schedule.** Rejected. That would
  rewrite realized PnL and hide missing venue evidence.
- **Pair twin signals by timestamp and invent unmatched fills.** Rejected. The comparison
  reports counts, windows, and divergence reasons only.
- **Persist a full per-bar execution trace beside every result.** Rejected. The immutable
  result and the verified signal trace already contain the facts, and an unbounded stored
  trace would be a second source of truth.
