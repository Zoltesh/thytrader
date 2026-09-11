# 0017: Maker-limit bar backtest as a new engine contract

- Status: Accepted
- Date: 2026-09-11
- Relates to: [0009](0009-deterministic-bar-level-backtest-engine.md), [0010](0010-constant-spread-backtest-provenance.md)

## Context

ADR 0009 requires a new engine-contract version whenever bar fills, unfilled expiry, or same-bar
exit ordering change. `thytrader-bar-backtest-v1` fills the next candle open with taker fees.
`thytrader-bar-backtest-v2` adds a constant-spread stress on that same next-open path.

Paper and live rest a post-only buy at the completed bar's close, wait up to `max_entry_wait_bars`,
cancel or reprice if unfilled, and can stop on the fill bar. Research that looks good on v1/v2 can
therefore miss or cancel live. Reinterpreting v1 or v2 would rewrite immutable fingerprints.

## Decision

Introduce `thytrader-bar-backtest-v3`. It is a new identity-bearing
`ResearchRunSpecification.engine_contract_version`, not a silent change to v1 or v2.

V3 run identity includes:

- `bar_execution.fill_timing`: `resting_maker_limit`
- `bar_execution.limit_at`: `completed_close`
- `broker.price_model`: `post_only_limit`
- `broker.fill_policy`: `resting_limit`
- `broker.trigger_evaluation`: `bar_extreme` (same OHLC extremes the worker uses)
- `broker.equity_marking`: `last_close`

Unfilled expiry (`cancel` / `reprice`) and `max_entry_wait_bars` remain on the published strategy
`execution` block; v3 consumes those fields instead of ignoring them. Entry fees use the run's
`maker_fee_rate`. Take-profit in v3 matches the worker's resting exit, not v2 high-touch.

V1 and v2 remain loadable and byte-identical. They still forbid maker fill timing and the v3 broker
literals. Simulation of v3 is a later increment of the same contract; publishing a v3 run without an
implemented kernel must fail closed rather than execute v1/v2 fills.

## Consequences

- Agents can name the maker contract in research without invalidating historical v1/v2 evidence.
- Paper/live comparison is only meaningful against v3 results once the kernel exists.
- A v3 fingerprint is distinct from an otherwise identical v2 run, including v2 with zero spread.

## Alternatives considered

- **Change v1/v2 fill timing in place:** rejected; ADR 0009 forbids reinterpretation.
- **Keep researching on next-open taker until live 5m exists:** rejected; that is the product gap
  this series exists to close.
- **Python strategy sandbox for maker logic:** rejected; stay on the declarative schema.
