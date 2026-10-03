# 0104: Bounded wait for a settling newest decision candle

- Status: Accepted
- Date: 2026-10-03
- Amends: [0095](0095-sparse-markets-no-trade-bars-listing-floors.md) (newest-bar pause)
- Relates to: [0033](0033-phase-10-risk-policy-registry.md), [0087](0087-per-bar-decision-timeline.md)
- Ops contract: v63; Alembic remains 0060

## Context

A provider can publish a newly closed candle shortly after its UTC boundary. Pausing immediately
makes an otherwise healthy book require an operator resume for a transient publication delay.

## Decision

Paper and live decision-clock processing may wait when exactly the newest closed candle is
missing, the prior window is contiguous, and the persisted evaluation cursor has no older gap.
The fixed deadline is that missing candle's UTC close plus 120 seconds. At the deadline, ordinary
`data_gap` pausing applies. Restarting or repeating a cycle never extends the deadline. No newest
candle is fabricated and no previous candle is substituted for an entry decision.

During the wait the deployment keeps its current lifecycle state, does not advance its evaluated
bar, and cannot evaluate or submit new entries. Strategy decisions record `skipped` with
`skip_reason: bar_settling`. Reconciliation and existing inventory/protection maintenance continue
through the existing no-entry maintenance path. A multi-instrument strategy waits as a whole if
any covered product's newest decision candle is settling, and maintains every covered book.
A historical gap on another covered product still pauses the strategy immediately.

Empty windows, older/multiple missing candles, expired waits, failed connections, reconciliation
mismatches, operator pauses, and breaker latches receive no exemption. Required HTF and additional
indicator-clock gaps retain their existing fail-closed policy; reference clocks retain their
existing per-entry gate. This wait changes only decision-clock publication handling, not strategy
semantics, synthetic stops, feed requirements, order authority, or risk limits. Existing resting
orders remain supervised by the normal maintenance and reconciliation rules.

## Alternatives

An unbounded wait could conceal broken market data. Synthesizing the newest bar could overwrite
late trades and change paper/live semantics. A process-local grace timer could reset on restart.
Waiting a complete strategy interval would strand slow-clock strategies for hours. A fixed,
short, absolute deadline avoids those failure modes without adding mutable risk configuration.
