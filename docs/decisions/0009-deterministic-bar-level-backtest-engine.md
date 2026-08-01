# 0009: Version bar-level backtest simulation separately from signal evaluation

- Status: Accepted
- Date: 2026-08-01

## Context

ADR 0008 made `thytrader-bar-signal-v1` an identity-bearing contract for deterministic indicators and entry-condition traces. Its accepted meaning excludes positions, fills, costs, exits, PnL, and result publication.

The first research simulation adds those stateful semantics. Assigning them to an existing signal-only run would silently change the meaning of published immutable content.

## Decision

Introduce `thytrader-bar-backtest-v1` as a new identity-bearing `ResearchRunSpecification.engine_contract_version`.

Only a newly published run using that contract may be simulated. The simulator rejects `thytrader-bar-v1` and `thytrader-bar-signal-v1`. The signal evaluator may calculate the signal stage for `thytrader-bar-backtest-v1`, but its trace preserves the backtest contract identifier rather than relabeling it as signal-only.

The V1 backtest contract is deterministic, long-only, and limited to one position. It derives signals from completed candles and models entries at the next candle open with adverse fixed slippage and taker fees. Its detailed execution ordering, result schema, Decimal rules, and persistence semantics are defined in [bar-level backtest simulation](../architecture/backtest-simulation.md).

## Consequences

- Existing request-only and signal-only run fingerprints remain immutable evidence with their original meaning.
- Otherwise equivalent backtest runs receive distinct fingerprints because their engine contract differs.
- Backtest output, including its engine-contract identifier, is content-addressed and append-only.
- Changes to bar fills, exit ordering, terminal valuation, sizing, costs, or result shape require a new engine-contract version rather than a reinterpretation of V1 results.

## Alternatives considered

- **Reuse `thytrader-bar-signal-v1`:** rejected because it retroactively adds simulation semantics to immutable runs.
- **Add fill/PnL options outside the run fingerprint:** rejected because ambient or command-line assumptions break reproducibility.
- **Embed backtesting in the signal evaluator contract:** rejected because it would couple a pure trace evaluator to stateful position accounting and persistence.
