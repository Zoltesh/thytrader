# Strategy and Backtesting Design

## Canonical strategy definition

A strategy is an immutable, versioned document validated by backend-owned schemas. UI forms, templates, backtests, paper execution, and live execution all use this definition. Published strategy versions are never mutated in place; editing creates a new version so results and live decisions remain reproducible.

The complete V1 field-level contract — indicators, conditions, entry, sizing, exits, execution, and validation layers — is specified in [canonical-strategy-schema.md](canonical-strategy-schema.md). That document is the implementation-facing specification; this document covers the runtime and simulation design.

The implemented Phase 2B publication profile validates the conservative 1h indicator catalog
(EMA, SMA, RSI, ATR, and volume SMA) and bounded recursive AND/OR/NOT conditions, publishes exact
canonical content immutably, and durably associates that strategy fingerprint with an independently
verified immutable dataset fingerprint. A narrow durable browser-authoring API now manages revision-
guarded drafts, publication, and archive markers; there is still no paper/live broker or order path.

The first Phase 3 prerequisite is also implemented: an internal immutable
[research-run specification](research-run-specification.md) binds the exact published strategy and
verified dataset to evaluation/warmup intervals, exact USD capital, maker/taker fees, fixed slippage,
completed-close/next-open timing, an explicit seed, and an explicit engine-contract version.
PostgreSQL publication is binding-gated and every load reverifies both source artifacts.

The first executable [signal evaluator](signal-evaluation.md) requires
`thytrader-bar-signal-v1`, calculates the bounded indicator catalog with deterministic Decimal
semantics, and emits a canonical per-candle entry-condition trace without lookahead. Historical
`thytrader-bar-v1` requests remain request-only. Separately, the implemented
[`thytrader-bar-backtest-v1` and `thytrader-bar-backtest-v2` simulator](backtest-simulation.md)
turns an eligible published run into an immutable long-only, single-position trade ledger, equity
curve, drawdown series, cost evidence, and result summary. It is still research-only: no broker
adapter, order intent, paper runtime, or exchange order path exists.

The browser API can author durable revision-guarded drafts, publish immutable strategy evidence,
archive publications through append-only markers, submit reproducible backtests, and inspect stored
immutable results. These narrow UI/API contracts preserve fingerprint verification, result
immutability, and the boundary between research and execution.

## V1 authoring experience

The V1 UI uses a structured rule builder with nested AND/OR groups. It should provide:

- type-aware operators and values;
- indicator parameter validation;
- human-readable summaries;
- reusable templates;
- validation before save or deployment;
- clear separation among signal, sizing, execution, and risk rules.

The browser builder at `/strategies/{strategy_id}` implements this for durable drafts: Overview,
Market and data, Indicators, Entry conditions, Exit conditions and protective stops, Position
sizing, Portfolio limits, and Execution preferences. Entry conditions are edited as a nested
ALL/ANY/NOT rule tree over comparisons and crossovers. An always-visible inspector shows a
plain-English summary, live validation errors, the required warmup/data window, unsaved-change
state, and an explicit engine-support matrix. That matrix distinguishes settings the current
`thytrader-bar-backtest-v1` and `thytrader-bar-backtest-v2` engines actually consume (entry
conditions, indicators, risk-fraction sizing with notional bounds, ATR initial stop, reward/risk
take profit, time exit) from declared schema fields neither engine models (entry cooldown,
maker-only/marketable preference, entry wait and unfilled policy, trailing stops). V2 alone supports
an explicit constant-spread stress assumption. Both engines fill every simulated entry at the next
bar open unconditionally, so unsupported fields can be declared but must never be read as backtested
behavior.

The library's read-only detail surface exposes Insight and Research tabs for every strategy identity.
Insight always shows the same summary, validation, warmup/data, unsaved/read-only state, and V1/V2
support matrix as the builder. Research requires an explicit immutable published version, verified
dataset, half-open evaluation period, exact initial capital, maker/taker fees, fixed slippage, and
engine contract; V2 additionally requires an explicit constant total bid-ask spread. It walks the
bounded results API until every stored result for each exact version is loaded, groups complete
history by version, and compares the newest result across versions. Drafts must be published before
research submission. Dataset-catalog and per-version result failures remain independently visible.

A future node-and-edge canvas may project the same schema. Advanced Python strategies may later implement a controlled plugin interface, but the built-in visual model must not depend on arbitrary code execution.

### First author-to-result vertical slice

The first authoring surface is intentionally constrained to the implemented conservative profile,
not a general-purpose strategy IDE. It must let a user create a draft from the reference template,
edit only fields supported by the current schema, validate errors before publication, publish one
immutable version, select a verified dataset, submit a reproducible backtest, and open the resulting
immutable evidence in the existing results screen.

Backtest submission must name a published strategy fingerprint and verified dataset fingerprint;
the server derives or validates all execution identity inputs and returns a result/run identity. A
browser or agent must not pass arbitrary code, bypass publication, mutate a published version, or
turn backtest submission into a paper/live deployment.

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
