# 0010: Version constant-spread stress assumptions as immutable backtest evidence

- Status: Accepted
- Date: 2026-08-03

## Context

`thytrader-bar-backtest-v1` models deterministic long-only bar simulation with adverse fixed slippage and taker fees. It has no observed bid/ask input. Adding an uncalibrated spread derived from OHLC range would create false precision, and silently changing V1 would invalidate immutable result evidence.

A useful immediate improvement is a transparent constant-basis-point stress assumption. It can show whether a strategy survives stated transaction friction, but cannot predict venue-specific historical or future fills without quote data.

## Decision

Introduce `thytrader-bar-backtest-v2`, not a second engine. V2 uses the existing deterministic signal stage and bar event ordering while requiring an immutable `broker` block in the research run.

The V2 broker block specifies a constant total `spread_bps`, full-fill policy, bid-side long exit triggers, and bid-close equity marking. Buys execute at the derived ask; sells execute at the derived bid; existing adverse slippage applies after the executable side. The broker block and engine version are canonical run/result evidence, so changing spread changes fingerprints.

Existing V1 runs and results remain byte-identical, loadable, and reverified. V2 with zero spread must reproduce V1 trade economics, but has distinct identity because its governing contract is different.

## Consequences

- Each V2 result discloses its assumptions and fill-level reference price, side, and spread cost.
- The read-only API/dashboard can present stored V2 assumptions rather than making prose claims about friction.
- V2 can alter which stop or target exits occur because long triggers evaluate bid-side candle extremes.
- Constant spread is explicitly a stress model, not empirical microstructure, observed Coinbase bid/ask data, or live-fill evidence.
- Future quote ingestion can introduce a later contract with dataset-backed bid/ask provenance without rewriting V1 or V2 evidence.

## Alternatives considered

- **Derive spread from OHLC range:** rejected because an uncalibrated alpha would look empirical while measuring no actual spread.
- **Modify V1 in place:** rejected because immutable published results must retain their original semantics.
- **Maintain separate V1/V2 simulation codebases:** rejected because it duplicates behavior and invites drift; one evolving engine branches only by immutable contract provenance.
- **Wait for quote data:** rejected because transparent stress analysis is valuable now, provided its limits are explicit.
