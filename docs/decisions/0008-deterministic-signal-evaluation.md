# 0008: Version deterministic signal evaluation separately from request-only runs

- Status: Accepted
- Date: 2026-07-30

## Context

ADR 0007 intentionally published immutable research requests before any executable engine existed.
Those documents use `thytrader-bar-v1`, whose accepted meaning was limited to request provenance and
completed-close/next-open timing. Assigning new indicator or condition semantics to that existing
identifier would silently change the meaning of already-published immutable content.

The next dependency is deterministic signal evaluation, but broker behavior, positions, exits, fills,
fees, slippage, and PnL are separate stateful concerns. Implementing all of them at once would make
lookahead, arithmetic, and event-order defects harder to isolate.

## Decision

Preserve `thytrader-bar-v1` as request-only and introduce `thytrader-bar-signal-v1` as a new
identity-bearing engine-contract value. Only the new value is executable by the signal evaluator.

The first executable engine:

1. reloads and reverifies the exact published run, strategy, dataset manifest, and Parquet candles;
2. calculates the canonical EMA, SMA, RSI, ATR, and volume-SMA catalog sequentially under the isolated
   `decimal64-half-even-v1` context: 64 significant digits, `ROUND_HALF_EVEN`, fixed exponent bounds,
   a defined subnormal output range, chronological left-fold accumulation, and traps for invalid
   operations, division by zero, and overflow;
3. evaluates bounded declarative entry conditions after completed candle closes, including prior/current
   crossover semantics and explicit undefined propagation;
4. excludes the next-open fill candle from every signal calculation; and
5. emits a canonical, fingerprinted, in-memory entry-condition trace.

The normative formulas, trace fields, and failure boundaries are defined in
[signal-evaluation.md](../architecture/signal-evaluation.md).

A matched condition is not an order intent. Cooldown, risk, exits, order modeling, fills, capital,
costs, and results remain outside this engine.

## Consequences

- Existing immutable `thytrader-bar-v1` publications remain loadable but fail closed if evaluation is
  requested.
- Executable requests receive distinct fingerprints because the engine-contract value is identity-bearing.
- Future indicator changes require another explicit engine-contract version rather than changing V1
  results in place.
- The same published request and verified artifacts recreate byte-identical trace output independent of
  ambient Decimal precision.
- At acceptance time, only a read-only CLI existed for executable publications; later research-workspace
  increments added bounded strategy authoring and mutation interfaces without changing this engine contract.
- Signal traces are ephemeral and are not evidence that a broker simulation or backtest result exists.

## Alternatives considered

- **Give `thytrader-bar-v1` executable semantics retroactively:** rejected because immutable identities
  would change meaning without changing bytes.
- **Implement broker/accounting and signals together:** rejected because stateful fills and positions
  would obscure the smaller no-lookahead and indicator contract.
- **Use binary floating point for speed:** rejected because deterministic cross-platform identity and
  exact audit values matter more than throughput for the initial bounded profile.
- **Persist traces immediately:** deferred because result lifecycle, storage shape, retention, and
  replay policy should be designed with broker/accounting outputs rather than prematurely treating a
  condition trace as a complete backtest result.
- **Allow arbitrary formulas or Python callbacks:** rejected because they violate the bounded canonical
  strategy and reproducibility model.
