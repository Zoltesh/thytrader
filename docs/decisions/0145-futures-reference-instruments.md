# 0145: Futures strategies may read USD-quoted spot reference instruments

- Status: Accepted
- Date: 2026-10-16
- Amends: [0128](0128-futures-backtest-model.md) (single-instrument futures, P1)
- Relates to: [0096](0096-reference-instruments.md), [0129](0129-paper-futures-books-and-shared-collateral-risk.md),
  [0134](0134-live-futures-order-path.md)

## Context

ADR 0128 kept futures documents single-instrument in P1 and refused reference instruments,
naming "another settlement currency" as the concern. Research on 2026-10-15 found that the one
trend rule surviving at futures cost reads a BTC daily reference regime, and it could not be
expressed as a futures strategy. The futures path already shares reference handling with spot:
backtest binding and the kernel's per-product signal evaluation, and the execution worker's
reference windows and per-bar gate, all of which are product-generic.

## Decision

1. **References are allowed on futures documents.** The P1 refusal is removed. A futures
   strategy declares `data_requirements.reference_instruments` and gates entries on reference
   indicators exactly as a spot strategy does (ADR 0096), in backtests, paper books and, once
   live futures exist, live books.
2. **USD-quoted only.** The existing rule that a reference uses the strategy's quote currency
   applies unchanged; a CFM contract settles in USD, so its reference is a USD spot product (for
   example `BTC-USD` 1d, the same Coinbase candles as `BTC-USDC`). The browser proposes BTC-USD
   for a futures strategy. No USD and USDC amounts are ever combined: references feed indicator
   values only, never capital, margin or sizing.
3. **Start checks apply as for spot.** A paper start needs the reference series watched and
   covering its warmup, and the start-history check includes reference windows.

## Consequences

- Futures documents without references keep their bytes and fingerprints; documents and runs
  with references get new identities, as any change does.
- Research studies still do not run futures (no `futures` assumptions on study requests); that is
  a separate change.
- Ops contract v105.
