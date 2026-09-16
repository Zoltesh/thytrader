# Strategy and Backtesting Design

## Canonical strategy definition

A strategy is an immutable, versioned document validated by backend-owned schemas. UI forms, templates, agent research tools, backtests, paper execution, and live execution all use this definition. Published strategy versions are never mutated in place; editing creates a new version so results and live decisions remain reproducible.

The complete V1 field-level contract — indicators, conditions, entry, sizing, exits, execution, and validation layers — is specified in [canonical-strategy-schema.md](canonical-strategy-schema.md). That document is the implementation-facing specification; this document covers the runtime and simulation design.

**Destination** ([ADR 0031](../decisions/0031-coinbase-first-platform-end-state.md)): many indicators
on every Coinbase-listed timeframe, plus single-asset **and** multi-asset deploy to paper or live.
**Shipped:** long or short, one primary instrument plus optional additional Coinbase USD spot products (at most eight total), `max_concurrent_positions` 1–8 (product books, not pyramid lots), optional intra-strategy pyramiding, venue LTF clocks ([ADR 0056](../decisions/0056-multi-instrument-documents-and-pyramiding.md)). Extra exchanges stay out.

The implemented Phase 2B publication profile validates the conservative indicator catalog
(EMA, SMA, RSI, ATR, volume SMA, highest, lowest, stdev, sample stdev, ROC, Williams %R, CCI, WMA, momentum, MFI, MACD, Bollinger, stochastic, ADX, identity OHLCV, and constant) and bounded recursive AND/OR/NOT conditions, publishes exact
canonical content immutably, and durably associates that strategy fingerprint with an independently
verified immutable dataset fingerprint. A narrow durable browser-authoring API now manages revision-
guarded drafts, publication, and archive markers. Paper and live execution consume the same published
version through `thytrader-execution-worker`: maker post-only entries, marketable stop/time-exits, and
fill-based live reconcile against Advanced Trade REST v3 JSON, paging List Fills by documented
cursor until exhausted and quarantining unparseable fill evidence
([ADR 0059](../decisions/0059-coinbase-list-fills-cursor-pagination.md)).

The first Phase 3 prerequisite is also implemented: an internal immutable
[research-run specification](research-run-specification.md) binds the exact published strategy and
verified dataset to evaluation/warmup intervals, exact USD capital, maker/taker fees, fixed slippage,
completed-close/next-open timing, an explicit seed, and an explicit engine-contract version.
PostgreSQL publication is binding-gated and every load reverifies both source artifacts.

The first executable [signal evaluator](signal-evaluation.md) requires
`thytrader-bar-signal-v1`, calculates the bounded indicator catalog with deterministic Decimal
semantics ([ADR 0026](../decisions/0026-phase-9-single-output-indicator-catalog.md),
[ADR 0027](../decisions/0027-phase-9-roc-williams-cci.md),
[ADR 0028](../decisions/0028-phase-9-identity-constant.md),
[ADR 0029](../decisions/0029-phase-9-wma-momentum-mfi.md),
[ADR 0032](../decisions/0032-phase-9-macd-bollinger.md),
[ADR 0047](../decisions/0047-wider-fail-closed-indicator-catalog.md)), and emits a canonical per-candle entry-condition trace without lookahead. Optional
`htf_filter` is AND-ed using last-completed HTF bars ([ADR 0025](../decisions/0025-multi-timeframe-htf-filter.md)).
Research V1/V2/V3, paper, and live consume that signal stage on last-completed HTF bars
([ADR 0041](../decisions/0041-paper-live-htf-filter-evaluation.md)). Optional per-indicator
timeframes overlay last-completed extra-TF values onto the LTF row before that AND
([ADR 0042](../decisions/0042-per-indicator-timeframes.md)). Historical
`thytrader-bar-v1` requests remain request-only. Separately, the implemented
[`thytrader-bar-backtest-v1`, `thytrader-bar-backtest-v2`, and `thytrader-bar-backtest-v3` simulator](backtest-simulation.md)
turns an eligible published run into an immutable single-position trade ledger, equity
curve, drawdown series, cost evidence, and result summary. V1/V2 stay next-open taker. V3 rests
maker limits the way paper and live do. The kernel still has no order authority.

**Engine contracts are not product releases.** V1 (next-open baseline), V2 (same as V1 plus constant
spread stress), and V3 (maker-limit realism aligned with paper/live) remain valid in parallel; V3 does
not retire V1/V2 evidence. Operator when-to-pick guidance lives in
[`skills/thytrader-research/SKILL.md`](../../skills/thytrader-research/SKILL.md). Do not confuse these
engine ids with Coinbase Advanced Trade REST v3.

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
`thytrader-bar-backtest-v1`, `thytrader-bar-backtest-v2`, and `thytrader-bar-backtest-v3`
engines actually consume. V1 and V2 consume entry conditions, optional HTF filter, the shipped
indicator catalog (EMA, SMA, RSI, ATR, volume SMA, highest, lowest, stdev, ROC, Williams %R, CCI,
WMA, momentum, MFI, MACD, Bollinger, identity OHLCV, constant), risk-fraction sizing with notional
bounds, ATR initial stop, reward/risk take profit, and time exit. V2 alone consumes an explicit
constant-spread stress assumption. V3 consumes the same HTF signal stage plus maker-only close-limit
entries, `max_entry_wait_bars`, `on_unfilled_entry`, same-bar stops, and resting take-profit, matching
the paper worker. V1 and V2 fill every simulated entry at the next bar open unconditionally; V3
does not. Entry cooldown remains unsupported on every bar engine. Optional ATR trailing uses the
same ratchet as paper/live; disabled trailing is a no-op. Walk-forward /
OOS / cross-market studies compose these engines ([research studies](research-studies.md)).
Validation kinds freeze one fingerprint; parameter sweeps and WFO select among published or
derived fingerprints without looking ahead ([ADR 0044](../decisions/0044-parameter-sweeps-wfo-stitched-equity.md)).
Richer axes and persisted catalog rows are [ADR 0052](../decisions/0052-richer-sweep-axes-study-catalog.md).
MACD/Bollinger conditions use series ids. Optional per-indicator timeframes
are shipped on V1/V2/V3, paper, and live. Paper and live consume the same LTF catalog, extra-TF
overlay, and HTF filter.
`POST /api/v1/strategies` accepts an explicit template id (`ema-trend` default;
`rsi-mean-reversion`, `macd-trend`, `bollinger-mean-reversion`). Templates are starting drafts, not
proven edges.

The library's read-only detail surface exposes Insight, Research, and Versions tabs for every
strategy identity. Insight always shows the same summary, validation, warmup/data, unsaved/read-only
state, and V1/V2/V3 support matrix as the builder. Research requires an explicit immutable published
version, verified dataset, half-open evaluation period, exact initial capital, maker/taker fees,
fixed slippage, and engine contract; V2 additionally requires an explicit constant total bid-ask
spread. The Research tab can launch a single window or a composed OOS / walk-forward / parameter-sweep
/ WFO study (cross-market stays on the research CLI). Maker/taker fields prefill from `GET /api/v1/fees` suggested rates when Coinbase credentials
exist (`suggestion_source=coinbase_fee_schedule`); the operator may override. Demo or missing
credentials leave the fields blank. Submitted rates are the research-run CostAssumptions, not
observed Coinbase fills. V1/V2 next-open fills still use the taker rate even when the strategy
prefers maker. It walks the bounded results API until every stored result for each exact version is loaded,
groups complete history by version, and compares the newest result across versions. Drafts must be
published before research submission. Dataset-catalog and per-version result failures remain
independently visible. Versions lists the complete immutable published history for one strategy
identity — each version with its fingerprint, archive marker, and newest backtest — and supports
three derived actions. Export downloads the exact canonical definition of one published version.
The semantic diff compares any two published versions field by field and renders human-readable
differences without JSON noise. `POST /api/v1/strategies/{strategy_id}/revise` derives the next
editable draft version (for example draft v2 from published v1) on the same stable strategy
identity from one selected immutable version, rejecting conflicting open drafts with HTTP 409;
publication remains immutable and history-preserving. Clone stays a separate-identity action at the
library level. `/deploy` starts paper or live runtimes for a published version, shows phase,
position, orders, fills, and reject reasons, and pause/resume/stop the execution worker. Live start
and stop ask for confirmation. Pause keeps protective exits running; stop cancels resting orders. The
library hover Archive action confirms the latest published version and fingerprint before appending
an archive marker; Clone stays ungated. The library paper/live column shows newest paper then live
status (`unavailable`, `running`, `paused`, or `stopped`) with a column legend; the cell opens
`/deploy`. Research launches from `/research`.

A future node-and-edge canvas may project the same schema. Advanced Python strategies may later implement a controlled plugin interface, but the built-in visual model must not depend on arbitrary code execution.

### First author-to-result vertical slice

The first authoring surface is intentionally constrained to the implemented conservative profile,
not a general-purpose strategy IDE. It must let a user create a draft from the reference template,
edit only fields supported by the current schema, validate errors before publication, publish one
immutable version, select a verified dataset, submit a reproducible backtest, and open the resulting
immutable evidence in the existing results screen.

Backtest submission must name a published strategy fingerprint and verified dataset fingerprint
(plus `htf_dataset_fingerprint` when the strategy declares `htf_filter`, and
`indicator_dataset_fingerprints` for unbound extra indicator clocks);
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
