# Strategy and Backtesting Design

## Canonical strategy definition

A strategy is one mutable object (a `strategies` row) whose document is validated by backend-owned schemas ([ADR 0082](../decisions/0082-strategy-root-mutable-strategies-auto-snapshots.md)). UI forms, templates, agent research tools, backtests, paper execution, and live execution all use this definition. You edit and save it in place under an optimistic-concurrency `revision`. Starting a backtest, study, or deployment snapshots the current definition into `strategy_snapshots`: canonical JSON addressed by its SHA-256 `strategy_fingerprint` and deduplicated. Results, studies, jobs, and bots record `strategy_id` plus that snapshot fingerprint, so results and live decisions remain reproducible without drafts, publication, or version numbers.

The complete V1 field-level contract — indicators, conditions, entry, sizing, exits, execution, and validation layers — is specified in [canonical-strategy-schema.md](canonical-strategy-schema.md). That document is the implementation-facing specification; this document covers the runtime and simulation design.

**Destination** ([ADR 0031](../decisions/0031-coinbase-first-platform-end-state.md)): many indicators
on every Coinbase-listed timeframe, plus single-asset **and** multi-asset deploy to paper or live.
**Shipped:** long or short, one primary instrument plus optional additional Coinbase USD spot products (at most eight total), `max_concurrent_positions` 1–8 (product books, not pyramid lots), optional intra-strategy pyramiding, venue LTF clocks ([ADR 0056](../decisions/0056-multi-instrument-documents-and-pyramiding.md)). Extra exchanges stay out.

The implemented Phase 2B publication profile validates the conservative indicator catalog
(EMA, SMA, RSI, ATR, volume SMA, highest, lowest, stdev, sample stdev, ROC, Williams %R, CCI, WMA, momentum, MFI, MACD, Bollinger, stochastic, ADX, identity OHLCV, and constant) and bounded recursive AND/OR/NOT conditions, snapshots exact
canonical content immutably at each start, and durably associates that snapshot fingerprint with an
independently verified immutable dataset fingerprint (`strategy_dataset_bindings`, keyed by
`strategy_id`). The browser-authoring API manages one revision-guarded strategy per id (invalid work
in progress is saved with its validation result). Paper and live execution consume the same
snapshot through `thytrader-execution-worker`: maker post-only entries, marketable stop/time-exits, and
fill-based live reconcile against Advanced Trade REST v3 JSON, paging List Fills by documented
cursor until exhausted and quarantining unparseable fill evidence
([ADR 0059](../decisions/0059-coinbase-list-fills-cursor-pagination.md)).

The first Phase 3 prerequisite is also implemented: an internal immutable
[research-run specification](research-run-specification.md) binds the exact strategy snapshot and
verified dataset to evaluation/warmup intervals, exact USD capital, maker/taker fees, fixed slippage,
optional spread stress, an explicit seed, and the single `engine: "thytrader-backtest"` identity.
PostgreSQL publication is binding-gated and every load reverifies both source artifacts.

The [signal evaluator](signal-evaluation.md) calculates the bounded indicator catalog with deterministic Decimal
semantics ([ADR 0026](../decisions/0026-phase-9-single-output-indicator-catalog.md),
[ADR 0027](../decisions/0027-phase-9-roc-williams-cci.md),
[ADR 0028](../decisions/0028-phase-9-identity-constant.md),
[ADR 0029](../decisions/0029-phase-9-wma-momentum-mfi.md),
[ADR 0032](../decisions/0032-phase-9-macd-bollinger.md),
[ADR 0047](../decisions/0047-wider-fail-closed-indicator-catalog.md)), and emits a canonical per-candle entry-condition trace without lookahead. Optional
`htf_filter` is AND-ed using last-completed HTF bars ([ADR 0025](../decisions/0025-multi-timeframe-htf-filter.md)).
Research, paper, and live consume that signal stage on last-completed HTF bars
([ADR 0041](../decisions/0041-paper-live-htf-filter-evaluation.md)). Optional per-indicator
timeframes overlay last-completed extra-TF values onto the LTF row before that AND
([ADR 0042](../decisions/0042-per-indicator-timeframes.md)). The
[backtest simulator](backtest-simulation.md) turns an eligible published run into an immutable trade
ledger, equity curve, drawdown series, cost evidence, and result summary. The kernel has no order
authority.

**One backtest model** ([ADR 0083](../decisions/0083-unified-backtest-model.md)). Every backtest uses
`engine: "thytrader-backtest"`; there is no engine selector. Signals evaluate on completed candles;
a matched signal rests a post-only limit at that candle's close and fills on a later candle that
trades through it (maker fee, no slippage), aligned with the paper and live workers. Stops, time
exits, and end-of-window liquidation are takers with fixed slippage. Optional `costs.spread_bps` is
a disclosed constant spread stress, not observed book data. `GET /api/v1/research/backtest-model`
(CLI `thytrader-research backtest-model`) describes these assumptions; operator guidance lives in
[`skills/thytrader-research/SKILL.md`](../../skills/thytrader-research/SKILL.md).

The browser API can create, save (revision-guarded), clone, import, and hard-delete strategies,
submit reproducible backtests and studies by `strategy_id` (the server snapshots the current
definition), and inspect stored immutable results. These narrow UI/API contracts preserve fingerprint verification, result
immutability, and the boundary between research and execution.

## V1 authoring experience

The V1 UI uses a structured rule builder with nested AND/OR groups. It should provide:

- type-aware operators and values;
- indicator parameter validation;
- human-readable summaries;
- reusable templates;
- validation before save or deployment;
- clear separation among signal, sizing, execution, and risk rules.

The browser builder at `/strategies/{strategy_id}` (Build) implements this and saves in place: Overview,
Market and data, Indicators, Entry conditions, Exit conditions and protective stops, Position
sizing, Portfolio limits, and Execution preferences. Entry conditions are edited as a nested
ALL/ANY/NOT rule tree over comparisons and crossovers. An always-visible inspector shows a
plain-English summary, live validation errors, the required warmup/data window, unsaved-change
state, the saved validation state, and a **How backtests simulate** disclosure
(`BacktestModelDisclosure`) listing the model's assumptions. The backtest model consumes entry
conditions, optional HTF filter, per-indicator timeframes, the shipped indicator catalog,
risk-fraction sizing with notional bounds, maker-only close-limit entries, `max_entry_wait_bars`,
`on_unfilled_entry`, ATR initial stop, reward/risk resting take profit, time exit, entry cooldown,
and enabled ATR trailing, matching the paper worker. Disabled trailing is a no-op. Walk-forward /
OOS / cross-market studies compose this model ([research studies](research-studies.md)).
Validation kinds freeze one snapshot; parameter sweeps and WFO select among snapshots of named
candidate strategies or derived variants (which keep the base `strategy_id`) without looking ahead ([ADR 0044](../decisions/0044-parameter-sweeps-wfo-stitched-equity.md)).
Richer axes and persisted catalog rows are [ADR 0052](../decisions/0052-richer-sweep-axes-study-catalog.md).
MACD/Bollinger conditions use series ids. Optional per-indicator timeframes
are shipped in research, paper, and live. Paper and live consume the same LTF catalog, extra-TF
overlay, and HTF filter.
`POST /api/v1/strategies` accepts an explicit template id (`ema-trend` default;
`rsi-mean-reversion`, `macd-trend`, `bollinger-mean-reversion`, and the wider-catalog templates
`donchian-breakout`, `supertrend-trend`, `squeeze-breakout`, `zscore-mean-reversion` from
[ADR 0086](../decisions/0086-indicator-catalog-expansion-and-offset.md)). Templates are starting
strategies, not proven edges. Indicator declarations may lag by `offset` bars (prior-bar channels,
"crossed since" patterns) in research, paper, and live alike.

The per-strategy workspace ([ADR 0080](../decisions/0080-per-strategy-workspace-build-test-run-why.md),
amended by [ADR 0082](../decisions/0082-strategy-root-mutable-strategies-auto-snapshots.md)) has Build,
Test, Run, and Why stages. Test requires a currently valid saved definition, a verified dataset,
half-open evaluation period, exact initial capital, maker/taker fees, and fixed slippage, with an
optional **Spread stress (bps)** under Advanced options (default 0). There is no engine picker. It can launch a single
window or a composed OOS / walk-forward / parameter-sweep / WFO study (cross-market stays on the
research CLI). Maker/taker fields prefill from `GET /api/v1/fees` suggested rates when Coinbase
credentials exist: the account's reported Coinbase rates (`suggestion_source=coinbase_account`,
[ADR 0090](../decisions/0090-research-correctness-optional-take-profit-diagnostics.md)), with the
pinned public schedule band as context only; the operator may override. Demo or
missing credentials leave the fields blank. Submitted rates are the research-run CostAssumptions, not
observed Coinbase fills. Test lists this strategy's results (`GET /api/v1/backtests?strategy_id=`); each row
shows **Current rules** when its snapshot `strategy_fingerprint` equals the strategy's
`current_fingerprint`, otherwise **Earlier edit** with a field-by-field "What changed" diff against
the snapshot (`GET /api/v1/strategies/snapshots/{strategy_fingerprint}`). Run starts paper or live
from the current definition and marks bots running an earlier edit; its guided **Update bot** is a
managed stop followed by a new start on the current rules, each confirmed (live keeps the
understand-live checkbox). Pause keeps protective exits running; stop defaults to managed shutdown.
Clone copies a strategy into a new identity; Import creates a new strategy from JSON. The library
has a checkbox column with page select-all, a bulk "Delete N strategies…" action with an accessible
confirmation dialog (dry run first; strategies with running or paused bots are blocked with the
reason; stopped live history is kept), per-strategy results for partial failures, a single-row
delete, and Build / Test / Paper / Live pipeline chips. Detached live bots of a deleted strategy show
"<name> (deleted strategy)".

A future node-and-edge canvas may project the same schema. Advanced Python strategies may later implement a controlled plugin interface, but the built-in visual model must not depend on arbitrary code execution.

### First author-to-result vertical slice

The first authoring surface is intentionally constrained to the implemented conservative profile,
not a general-purpose strategy IDE. It must let a user create a strategy from the reference template,
edit only fields supported by the current schema, see validation errors, save, select a verified
dataset, submit a reproducible backtest (which snapshots the saved definition), and open the
resulting immutable evidence in the existing results screen.

Backtest submission must name a `strategy_id` whose saved definition is valid and a verified dataset fingerprint
(plus `htf_dataset_fingerprint` when the strategy declares `htf_filter`, and
`indicator_dataset_fingerprints` for unbound extra indicator clocks);
the server derives or validates all execution identity inputs and returns a result/run identity. A
browser or agent must not pass arbitrary code, bypass snapshotting, mutate a stored snapshot, or
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
- reproducible strategy, dataset, and engine identity.

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
