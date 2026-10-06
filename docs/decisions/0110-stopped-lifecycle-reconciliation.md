# 0110: Stopped-book reconciliation and missing-candle protection

- Status: Accepted
- Date: 2026-10-06
- Amends: [0058](0058-protection-lifecycle-accounting.md) (stop versus flatten while data is missing)
- Relates to: [0057](0057-atomic-fill-ledger-and-product-isolation.md),
  [0039](0039-on-demand-discretionary-trades.md),
  [0104](0104-bounded-newest-candle-wait.md)

## Context

Three fail-closed paths stopped supervising live inventory when the book was already paused,
when the decision window had no candles, or when a stopped deployment was not the primary
strategy book.

`reconcile_open_orders` returned after the first watched order whenever status was already
`PAUSED`, so later orders and attached children were never read. `_advance_strategy` returned
before live reconciliation when the closed window was empty. `flatten_stopped_residual` then
cancelled every resting order, including protection, without submitting an exit. `_process_stopped`
ignored discretionary shutdown and any book that was not the focused primary position, and it
could treat a book as flat before live fills and cancel races were known.

## Decision

1. Reconciliation walks every watched order and then imports attached children even when the
   deployment is already paused or stopped. Faults from initial fill replay, ordinary order
   reads, and child import are retained while healthy siblings still reconcile. A known child
   identity is persisted as unknown before its first read; a failed read is not an absent child.
   Reconciliation never unpauses an operator pause. Even missing-metadata entry fill projection
   keeps a stopped shutdown `STOPPED`, rather than briefly persisting `PAUSED`.
2. An actually empty decision-candle window journals a data-gap skip and pauses a running book,
   with no new entries. Live order and fill reconciliation still runs. Native protection may
   use persisted stop/target geometry with a fresh venue context, without signal/ATR evaluation
   or advancing the decision cursor. No candle or price is fabricated. Bounded cold-cache
   rebuilding raises the shared `market_data.window_state.WindowCacheWarmingError`: this is not
   evidence of absent venue data, does not change the operator's lifecycle choice or cursor,
   and still runs no-entry reconciliation/protection. The windows lane imports this same type.
   Every maintenance fallback is product-scoped. Already persisted live signal/flatten
   decisions and reached time exits precede re-resting protection during warming; incomplete
   history does not reverse a durable exit or advance the decision cursor.
3. Flatten without a verified closed price keeps protective orders, may cancel only entry
   intents, and records `Flatten is pending: no verified closed price is available...`. That
   state is not success. A genuine reconciliation fault remains visible instead of being
   overwritten by this wait detail. A provider-returned candle, including a product preview,
   may price the risk-reducing exit only for the enabled matching product with verified venue
   increments and the most-recent closed, positive-volume traded bar. Historical, in-progress,
   disabled-product, and synthetic no-trade contexts cannot authorize stripping protection.
4. Stopped supervision lives in `execution/stopped.py`. It reconciles live orders and fills
   before deciding a book is flat, then applies managed shutdown or explicit flatten to every
   product book, including a discretionary book and a sole secondary position. A cancel that
   races a fill is reconciled before another exit is sent. An unknown cancel stays supervised
   across worker restarts. Canceled orders remain watched under shutdown even if the currently
   known partial fills are already applied; partial locally-filled fragments are not a final
   venue watermark. Unapplied fills and filled protection awaiting REST evidence block another
   cover and prevent settlement. This also applies to canceled orders whenever the reported
   executed quantity exceeds applied REST-fill coverage, including partial publication.
   Venue-reported executed quantities are monotone evidence, not cleared by a later cancel
   acknowledgement. Managed shutdown still cancels risk-increasing entries and
   keeps protection; flatten exits then cancels remainders once a verified price exists.
5. Shared atomic fill projection explicitly focuses `order.product_id`. Completing one book
   returns no focused position while retaining siblings in `positions[]`; stores must not
   write the next sibling into the closed product. Historical applied-fill evidence and fees
   are not rewritten. Idle stopped runtime phases may settle only after observed inventory
   and active orders are gone. An unavailable immutable strategy snapshot does not terminate
   stopped supervision: stored protection remains supervised with an explicit fault detail.
   An applied entry without position metadata remains unknown inventory. Retained intent-backed
   entry orders and applied fills anchor exact per-product signed inventory; a net balance
   not represented by the position projection blocks protection cancellation and flat
   settlement independently of the latest display fault. Pre-ledger legacy exits cannot
   offset a later owned entry. No stop geometry, executable quantity, or repair of historical
   cash/fees is inferred from this safety predicate.

## Consequences

- No schema or ops-contract change. Operator-visible `mismatch_detail` gains the pending-flatten
  sentence above and may keep an existing pause detail while sibling orders are reconciled.
- `execution/stopped.py` is the worker's stopped-book entry point. `flatten_stopped_residual`
  refuses to strip protection when `candles` is empty.
- Lead still owns ops-contract, operator schema, architecture index, and roadmap integration.
- A top-of-book quote is not used as an exit price. Only a fresh provider traded candle is.
- Without any fresh price context, existing native protection is retained; synthetic trails,
  paper bar matching, and signal/time exits cannot be invented from missing market data.
  This is an honest supervised pending state, not a claim that the position has been exited.

## Alternatives considered

- Returning early on any new pause: rejected, because the next cycle is then paused and would
  skip the same later orders forever.
- Inventing a flat candle or using an unverified mark so flatten can always exit: rejected;
  that fabricates PnL and can sell at a price the venue did not print.
- Treating a stopped discretionary book as terminal: rejected; residual inventory must stay
  supervised until it is flat or an operator transfers it.
