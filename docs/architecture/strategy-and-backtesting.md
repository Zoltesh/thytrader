# Strategy and Backtesting Design

## Canonical strategy definition

A strategy is an immutable, versioned document validated by backend-owned schemas. UI forms, templates, backtests, paper execution, and live execution all use this definition. Published strategy versions are never mutated in place; editing creates a new version so results and live decisions remain reproducible.

The complete V1 field-level contract — indicators, conditions, entry, sizing, exits, execution, and validation layers — is specified in [canonical-strategy-schema.md](canonical-strategy-schema.md). That document is the implementation-facing specification; this document covers the runtime and simulation design.

The implemented Phase 2B publication profile validates the conservative 1h indicator catalog
(EMA, SMA, RSI, ATR, and volume SMA) and bounded recursive AND/OR/NOT conditions, publishes exact
canonical content immutably, and durably associates that strategy fingerprint with an independently
verified immutable dataset fingerprint. There is no authoring endpoint, signal evaluator, backtest
engine, broker, or order path yet.

The first Phase 3 prerequisite is also implemented: an internal immutable
[research-run specification](research-run-specification.md) binds the exact published strategy and
verified dataset to evaluation/warmup intervals, exact USD capital, maker/taker fees, fixed slippage,
completed-close/next-open timing, an explicit seed, and the literal `thytrader-bar-v1` request-contract
version. PostgreSQL publication is binding-gated and every load reverifies both source artifacts. This
is a reproducible request contract only; it does not evaluate signals or produce backtest results.

## V1 authoring experience

The V1 UI uses a structured rule builder with nested AND/OR groups. It should provide:

- type-aware operators and values;
- indicator parameter validation;
- human-readable summaries;
- reusable templates;
- validation before save or deployment;
- clear separation among signal, sizing, execution, and risk rules.

A future node-and-edge canvas may project the same schema. Advanced Python strategies may later implement a controlled plugin interface, but the built-in visual model must not depend on arbitrary code execution.

## Reference strategy

The initial end-to-end strategy is a configurable EMA trend strategy:

- fast EMA crossing a slow EMA;
- optional RSI and volume filters;
- ATR-based initial stop;
- configurable reward/risk take-profit;
- ATR- or percentage-based trailing stop;
- volatility-aware position sizing;
- post-only limit entry with timeout and repricing rules.

Its purpose is to exercise the platform, not to promise profitability.

## Shared event model

Backtest, paper, and live runtimes should share domain events and order semantics where possible:

1. Market data becomes a normalized event.
2. The strategy evaluates only information available at that event time.
3. A signal produces an order intent, not an exchange call.
4. Risk policies approve, resize, or reject the intent.
5. A broker adapter models or submits the order.
6. Order/fill events update portfolio and strategy state.
7. Every transition is persisted and auditable.

The backtester must not import the live Coinbase client. Both depend on a provider-neutral broker contract.

## Backtest fidelity

### Required baseline

- deterministic clock and random seed where randomness is used;
- strict prevention of lookahead bias;
- maker and taker fees;
- spread and configurable slippage;
- configurable latency;
- precision and minimum-size constraints;
- limit-order timeout/cancel behavior;
- partial fills and rejected orders;
- portfolio cash and exposure constraints;
- SL/TP and trailing lifecycle;
- gaps and missing-data policy;
- reproducible strategy, dataset, and engine versions.

### Fidelity levels

1. **Bar level:** fast research with explicit, conservative OHLC fill assumptions.
2. **Trade/tick level:** more precise event ordering and liquidity modeling when data exists.
3. **Order-book replay:** future high-fidelity mode with queue assumptions and substantially higher data/storage cost.

A limit touched within a candle is not proof of a maker fill. The default bar model should require conservative price crossing and clearly report the assumption.

## Results

At minimum, results include:

- equity and drawdown series;
- realized and unrealized P&L;
- fees, slippage, and turnover;
- exposure and utilization;
- trade/fill ledger;
- win rate and expectancy;
- profit factor;
- Sharpe and Sortino ratios with stated annualization assumptions;
- maximum drawdown;
- parameter and dataset fingerprints;
- warnings about data gaps or unsupported assumptions.

Walk-forward and out-of-sample workflows are preferred over tuning against one full historical period.
