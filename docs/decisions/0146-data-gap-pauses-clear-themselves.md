# 0146: A decision-window data-gap pause clears itself once the window is whole

- Status: Accepted
- Date: 2026-10-18
- Amends: [0104](0104-bounded-newest-candle-wait.md) (the deadline pause),
  [0095](0095-sparse-markets-no-trade-bars-listing-floors.md) (confirmed no-trade bars)
- Relates to: [0113](0113-deploy-anchored-window-cache.md), [0115](0115-durable-safety-alerts-and-supervision.md)

## Context

On a thin market (for example a Coinbase perp with a quiet two hours) Coinbase publishes no
candle for an interval without trades. The newest bar is then missing at the close; after the
bounded wait (ADR 0104) the worker pauses the book with "Market-data window is gapped or missing
the latest closed bar." One bar later the next candle publishes, the hole becomes a confirmed
no-trade bar (ADR 0095) and the deploy window is whole again. Nothing cleared the pause: the
book kept evaluating bars while paused, without entries, and the journal labelled every one
`DATA_GAP` from the pause reason. Thin perp paper books stayed paused for hours until an operator
resumed them (two of seven books on 2026-10-18, five times in three days).

## Decision

When a book's due bars are evaluated on a complete decision window, a pause whose **only** reason
is the decision-window gap is cleared automatically, before evaluation: status RUNNING, mismatch
cleared, audit `data_gap_pause_cleared`. "Only" means the exact decision-window detail, no
lifecycle command, and no daily-loss or drawdown latch, as for the user-feed pause.

- Operator pauses, lifecycle commands, breaker latches and every other mismatch never clear.
- HTF, indicator-timeframe and multi-instrument coverage pauses keep their own details and stay
  sticky; a still-gapped HTF window re-pauses under its own detail, so the book cannot flap.
- Catch-up bars after a gap still never enter; only the newest due bar may.
- The deadline pause itself is unchanged: a quiet newest bar still costs one bar without entries.

## Consequences

- Thin markets lose one bar per quiet interval instead of every bar until an operator resumes.
- A live book resumes the same way; protection and reconciliation ran throughout the pause.
- The journal shows the gap bar as `DATA_GAP` and later bars as evaluated again.
