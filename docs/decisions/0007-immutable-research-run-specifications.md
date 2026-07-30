# 0007: Publish immutable research-run specifications before simulation

- Status: Accepted
- Date: 2026-07-29

## Context

An immutable strategy fingerprint and immutable dataset fingerprint identify two inputs, but they do
not identify a reproducible experiment. Starting capital, fees, slippage, evaluation boundaries,
warmup, event timing, engine semantics, and random seed can otherwise drift through ambient
configuration. Building a simulation kernel first would allow results to exist without a complete,
auditable request contract.

## Decision

Define and publish one strict canonical research-run specification before implementing signal or fill
simulation. The V1 document binds an exact published strategy and its exact verified dataset binding
to half-open hourly evaluation and warmup intervals, exact USD capital and cost assumptions, a fixed
completed-close to next-open timing convention, the literal `thytrader-bar-v1` request-contract
identifier, and an explicit deterministic seed.

Publication is append-only and content-addressed. PostgreSQL stores canonical specifications only after
revalidating the typed model and reverifying the strategy, immutable dataset, existing binding, coverage,
and compatibility. Every load repeats those checks. The detailed implemented contract is specified in
[research-run-specification.md](../architecture/research-run-specification.md).

This decision creates no simulation, broker, order, paper, or live-trading authority.

## Consequences

- Future simulation work receives one complete immutable request instead of ambient assumptions.
- The final evaluation candle requires one additional verified candle for its possible next-open fill.
- Equivalent decimal spellings share identity while distinct high-precision values remain distinct.
- A missing or corrupted strategy or dataset makes an existing publication row ineligible at load time.
- Run-request schema and engine-contract evolution become explicit compatibility responsibilities.
- UUIDv7 request identity is bound to the document's UTC creation millisecond rather than merely
  carrying valid version bits.
- Results, metrics, spread, latency, fill state machines, and execution remain unimplemented.

## Alternatives considered

- **Build the simulation kernel first:** rejected because experiments could be produced before their
  assumptions and artifact provenance were immutable.
- **Store loose database columns without canonical JSON:** rejected because identity and replay would
  depend on database representation rather than one portable document.
- **Allow arbitrary engine-version strings:** rejected because a future-looking label could imply
  semantics that ThyTrader has not implemented.
- **Treat a strategy/dataset binding as a run:** rejected because it omits evaluation boundaries,
  warmup, capital, cost, timing, and seed assumptions.
