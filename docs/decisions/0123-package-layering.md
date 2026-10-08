# ADR 0123: Declared package layers with shrink-only upward imports

- Status: Accepted
- Date: 2026-10-08
- Related: [ADR 0083](0083-unified-backtest-model.md) (one evaluation model for every mode)

## Context

The 2026-10 module splits shortened files but left GitNexus mean community cohesion at about
0.65, because cohesion is measured on the call graph and moving code between files does not
change it. The repo-wide mean is also a poor target: at this repo's size GitNexus clusters at
high resolution, so cohesive files split into many 3–5 symbol fragments.

The structural problem was that there was no layering. 27 of the 38 top-level packages
imported each other in a single cycle. Domain packages imported Postgres stores and the audit
store from `persistence`. `backtest` and `execution` imported the run specification and the
signal evaluator from `research`. `risk` imported the trading model from `execution`, and
settings imported domain enums.

## Decision

1. `tests/package_layers.json` declares the layers, highest first: processes, interfaces,
   services, adapters, coordination, research, simulation, execution, risk, evaluation,
   contracts, platform, market, foundation. A module belongs to its top-level package unless a
   `components` pattern claims it. Agent CLIs and their loopback clients are an `agent_cli`
   component at the interface layer. The Coinbase broker is a `venue_adapters` component
   beside persistence.
2. A module may import its own layer or a lower one. `tests/test_package_layers.py` fails on
   a new upward import, on growth past a recorded ceiling, on a ceiling above the current
   count, and on same-layer cycles. Imports under `TYPE_CHECKING` and inside functions count.
3. The run specification, indicators, closed-candle multi-timeframe alignment, signal
   evaluation, traces, stresses and publication eligibility form the `evaluation` package. It
   sits below backtest, research and execution, so every mode consumes the same semantics
   without importing research orchestration.
4. GitNexus cohesion is judged per production community of 10+ symbols (at or above 0.8),
   not by the repo mean.

## Consequences

- The remaining upward imports are the decoupling work list. `risk -> execution` needs a
  trading-model package below risk. Research, backtest and fleet control must reach
  Postgres through protocols wired by processes.
- Moves keep definitions AST-identical and OpenAPI byte-identical. Old import paths are
  retargeted rather than kept as shims.
