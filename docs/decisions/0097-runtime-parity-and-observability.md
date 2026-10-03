# 0097: Paper follows the backtest's same-bar exit precedence; position state and paper/live fill comparison

- Status: Accepted — pairing amended by [0102](0102-explicit-paper-live-twin-links.md) (amended by [0098](0098-library-views-book-marks-portfolio-fills.md))
- Date: 2026-10-02
- Amends: [0083](0083-unified-backtest-model.md) (paper now matches the stop-first rule on a
  time-exit bar), [0093](0093-signal-based-exits.md) (the precedence below is the one contract),
  [0058](0058-protection-lifecycle-accounting.md) (adds `position_state` / `exit_in_flight` next to
  `protection_status`), and [0088](0088-portfolio-model-and-portfolio-backtest.md) / [0091](0091-portfolio-deployment-limits-and-manager-proposals.md) (the
  operator `portfolios` report gains a paper/live fill comparison)
- Relates to: [0082](0082-strategy-root-mutable-strategies-auto-snapshots.md) (twins match by
  snapshot fingerprint), [0090](0090-research-correctness-optional-take-profit-diagnostics.md),
  [0092](0092-research-worker-pool.md); ops contract v57 (no migration)

## Context

Four operator-visible gaps showed up while running paper and live twins side by side:

1. **Paper and backtest disagreed on a stop/time-exit tie.** When the protective stop and the
   `max_bars_held` time exit fell on the same bar, paper skipped its stop check and sold at the close
   as a `time_exit`, while the backtest took the stop. The backtest's rule (ADR 0083) is the
   conservative one: the candle cannot say whether the stop traded before the close.
2. **`phase: pending_exit` read as "exiting".** The worker sets `pending_exit` as soon as any exit
   order works, which includes the TP/SL bracket (or ADR 0090 stop-only protection, or the paper
   take-profit) resting right after entry. Operators read it as a book that was closing.
3. **Async study submits reported an alarming error.** The API plans the study and auto-binds its
   datasets before it queues the job. That can exceed the CLI's 5 s client timeout on a large sweep.
   The study persisted, but the agent saw an "ambiguous submit" failure.
4. **No way to compare paper and live entry fills.** A live post-only entry filled on Coinbase in
   about 5 s. The identical paper twin waited for a closed candle to trade through its limit. Nothing
   showed that difference.

## Decision

### Same-bar exit precedence (paper equals backtest)

For every bar after the fill bar, both modes resolve exits in this order:

1. The protective stop, checked against the pre-trail level (the ATR trail ratchets after the check).
2. A take-profit the bar touched (paper does not match a resting TP on a bar that also trades
   through the stop).
3. The `exits.signal_exit` rule, at the close.
4. The `max_bars_held` time exit, at the close.

On the fill bar only the stop is eligible. Paper now always checks its synthetic stop first, even
when the time exit is also due. Live keeps its venue brackets, which the venue resolves. The ops
contract advertises the order as `same_bar_exit_precedence`. A parametrized parity test runs one
strategy and one candle series through the backtest kernel and the paper loop for every
combination: stop+TP, stop+time, stop+signal, gapped stop+time, TP+signal, TP+time, signal+time,
trailed stop+time/TP/signal, the fill-bar stop+TP, and a trail ratchet on a time-exit bar. Both modes
must take the same exit, on the same bar, at the same price.

### Position state next to the raw phase

`phase` is unchanged: it is the worker's state machine, and existing consumers keep reading it.
Deployment HTTP bodies, each `positions[]` row, operator `strategies` / `runtime` deployment rows and
their `books[]`, and portfolio sleeve bots add:

- `position_state`: one of `flat`, `entering`, `open_protected`, `open_unprotected`,
  `open_unverified`, or `exiting`. A deployment takes its worst book, in the order exiting, then
  unprotected, then unverified, then protected.
- `exit_in_flight`: true only while an exit is being sent. That means a working marketable order on
  the closing side, the ADR 0093 signal-exit marker, or a flatten request. A resting TP, bracket, or
  stop-limit is protection, not an exit.

An open paper book that is not exiting is always `open_protected`: the worker enforces its stop
synthetically on every closed bar, and any take-profit rests in the paper broker. Live books map
`protection_status`: `covered` is `open_protected`, `unprotected` is `open_unprotected`, and
`unknown` is `open_unverified`. The classification uses order sides, not intents, so bounded summary
reads classify the same way as full reads. The UI shows "Open · protected (TP/SL resting)" or
"Exiting" instead of the raw phase on bot detail, Portfolio sleeves, and Home.

### Async study submit timeout

`thytrader-research submit-study --async` now waits 30 s for the API's 202 (it was 5 s).
`--submit-timeout-seconds` (1 to 300) overrides the wait in either mode. Synchronous submits keep
their 60 s client wait. Planning stays in the API. Moving it into the worker would change the
submit response (bound datasets, evaluation window) and the error surface for invalid plans. Raising
the client wait is the smaller safe change. A timeout is still treated as ambiguous and names the
`list-studies` readback.

### Paper/live entry-fill comparison

The operator `portfolios` report adds `paper_live_fill_comparisons`. It pairs the newest paper and
the newest live strategy book bound to the same `strategy_fingerprint` (the content address of the
exact rules, ADR 0082), whether those books are portfolio sleeves or standalone. At most ten pairs
are listed, newest first. Each side reports:

- entries rested, filled, expired (canceled unfilled), rejected, and working;
- `average_fill_vs_limit_bps`, where positive means worse than the posted limit;
- average and median seconds to fill.

Paper waits run to the fill bar's close, because that is when the worker can first see the fill.
Live waits end at the venue fill. The report is read-only and never touches orders.

## Consequences

- Paper's realized stop and time-exit counts can shift to match the backtest. Strategies whose time
  exit often coincided with a stop-through bar now record more `stop` exits in paper.
- Agents and the UI should read `position_state` / `exit_in_flight`. `phase: pending_exit` alone no
  longer means anything is exiting.
- The fill comparison loads full snapshots for at most ten twin pairs per report. Very long-running
  bots make the report heavier, but it stays bounded.
- Deferred: an explicit paper/live link (twins are matched by content only), the comparison in the
  Portfolio UI, and moving study planning into the research worker.

## Alternatives considered

- **Split `pending_exit` into new phases.** Rejected: every phase consumer (risk exposure, operator
  occupancy, discretionary books, existing agents) would change. Derived fields keep `phase`
  compatible.
- **Let the time exit win in the backtest instead.** Rejected: assuming the close came before a
  stop the bar traded through is optimistic and contradicts ADR 0083's conservative rule.
- **Move study planning into the worker.** Rejected for now: it is a larger change to the submit
  contract than this problem needs.
