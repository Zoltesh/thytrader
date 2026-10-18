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
`DATA_GAP` from the pause reason. Books on thin markets could stay paused for hours, after every
quiet interval, until an operator resumed them.

## Decision

When a book's due bars have been evaluated on a complete decision window, a pause whose **only**
reason is the decision-window gap is cleared automatically, after that evaluation: status RUNNING,
mismatch cleared, audit `data_gap_pause_cleared` (naming the last evaluated bar). "Only" means the
exact decision-window detail, no lifecycle command, and no daily-loss or drawdown latch, as for
the user-feed pause. The clear re-reads the book, so the evaluation's writes stand.

- Operator pauses, lifecycle commands, breaker latches and every other mismatch never clear.
- HTF, indicator-timeframe and multi-instrument coverage pauses keep their own details and stay
  sticky; a still-gapped HTF window re-pauses under its own detail, so the book cannot flap.
- Catch-up bars, and the newest bar of the recovering cycle, are evaluated while still paused:
  no entry and no repricing of a working entry. Entries resume from the next due bar.
- The deadline pause itself is unchanged.

## Consequences

- Thin markets lose about two bars of entries per quiet interval instead of every bar until an
  operator resumes.
- A live book resumes the same way; protection and reconciliation ran throughout the pause.
- The decision journal keeps one row per bar, so the gap bar's row is later replaced by its
  catch-up evaluation; the recovery audit records the bar the pause was cleared through.
