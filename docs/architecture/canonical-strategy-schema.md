# Canonical Strategy Schema

> **Status: Partially implemented V1 contract.** The conservative reference profile described
> below now has backend validation, immutable PostgreSQL publication, verified fingerprint loading,
> and exact binding to a verified immutable dataset fingerprint. This creates no order authority.
> The broader authoring contract remains proposed until the unsupported variants below are built.
>
> **Product destination** ([ADR 0031](../decisions/0031-coinbase-first-platform-end-state.md)):
> strategy `timeframe` includes every Coinbase-listed candle granularity ThyTrader ingests
> ([ADR 0040](../decisions/0040-venue-strategy-paper-live-htf-clocks.md)), and deployments cover
> single-asset **and** multi-asset Coinbase USD spot paper/live
> ([ADR 0056](../decisions/0056-multi-instrument-documents-and-pyramiding.md)). Extra exchanges stay
> out. The field rules in this document are the **shipped contract**. Mermaid overview:
> [contract diagrams — strategy](contracts/strategy.md).

This document is the implementation-facing specification referenced by
[ADR 0005](../decisions/0005-canonical-strategy-schema.md). The ADR records the decision; this
document defines the contract.

## Current implementation boundary

The implemented Phase 2B publication profile remains deliberately narrow and fail closed:

- frozen models with unknown-field rejection, UUIDv7 identity, UTC timestamps, string-only finite
  decimals normalized to plain canonical text, bounded values, unique indicator IDs, reference
  resolution, and warmup validation;
- every ingested venue clock (`1m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `1d`) for research,
  backtests, paper, and live; long or short, one primary instrument plus optional additional USD
  spot products (at most eight total), with EMA/SMA/RSI/ATR/volume-SMA/`highest`/`lowest`/`stdev`/`stdev_sample`/`roc`/`williams_r`/`cci`/`wma`/`momentum`/`mfi`/`macd`/`bollinger`/`stochastic`/`adx`/`identity`/`constant` indicators;
- optional `htf_filter` (ADR 0025, ADR 0041) for research, paper, and live: HTF `when` AND-ed with LTF entry using the last completed HTF bar;
- bounded recursive `all`/`any`/`not` groups of typed comparisons, risk-fraction sizing,
  ATR-multiple initial stop, reward/risk take profit, optional ATR trailing stops, an optional
  `exits.signal_exit` rule tree ([ADR 0093](../decisions/0093-signal-based-exits.md)), and
  conservative maker preferences;
- canonical sorted compact JSON and `sha256:<hex>` identity over the entire snapshotted document;
- one mutable `strategies` row per `strategy_id` (document, validation result, `current_fingerprint`,
  revision) plus content-addressed, deduplicated `strategy_snapshots` rows written automatically when
  a backtest, study, or deployment starts, and exact `strategy_dataset_bindings` rows keyed by
  `strategy_id` ([ADR 0082](../decisions/0082-strategy-root-mutable-strategies-auto-snapshots.md));
  a concurrent snapshot of identical content is a no-op, and loading re-verifies canonical bytes,
  the fingerprint, and Coinbase provider, product, and timeframe compatibility.

The dataset root is a private, worker-owned local trust boundary. Verification and binding have a
bounded verify-then-persist TOCTOU window under that assumption. A binding row records an accepted
association, not permanent consumability; every binding load re-verifies both exact artifacts.

Implemented: optimistic-concurrency strategy persistence (invalid work in progress is saved with
its validation result), browser authoring API/UI, automatic immutable snapshots, completed reproducible backtest results (the single
`thytrader-backtest` model's maker-limit fills, [ADR 0083](../decisions/0083-unified-backtest-model.md)), paper and live execution on closed venue bars,
and optional ATR-multiple trailing stops. Paper and live evaluate `htf_filter` on last-completed
complete-only HTF bars. Not yet implemented: other sizing/stop/trailing variants or richer human
summaries. Published research runs support read-only deterministic
entry-condition evaluation as defined in
[Signal Evaluation](signal-evaluation.md). Unsupported shapes are rejected rather than approximated.

## Design principles

1. **One schema, every runtime.** Backtest, paper, and live consume the same immutable snapshot.
2. **Declarative, not executable.** No Python, JavaScript, arbitrary expressions, or UI layout data.
3. **Decimal-precise.** All monetary and quantity values are strings, consistent with existing
   ThyTrader financial boundaries.
4. **Explicit and bounded.** Every field has a type, allowed range, and defined invalid behavior.
5. **Reproducible.** A strategy snapshot fingerprint + dataset fingerprint + canonical research-run specification
   must disclose every implemented assumption needed to reproduce a future backtest result.
6. **No false authority.** A validated strategy is not a profitable strategy and not a live order.

## Top-level document

```json
{
  "schema_version": "1.0",
  "strategy_id": "01978a3e-5f2c-7d10-b3a4-000000000001",
  "name": "EMA trend reference",
  "description": "Optional operator-facing description.",
  "created_at": "2026-07-28T12:00:00Z",
  "instrument": {
    "product_id": "BTC-USD",
    "base_currency": "BTC",
    "quote_currency": "USD"
  },
  "timeframe": "1h",
  "data_requirements": {
    "warmup_bars": 250,
    "required_fields": ["open", "high", "low", "close", "volume"]
  },
  "indicators": [],
  "entry": {},
  "sizing": {},
  "portfolio_limits": {},
  "exits": {},
  "execution": {},
  "metadata": {}
}
```

The outline above is not canonical bytes. `htf_filter` is omitted from canonical JSON when null so
existing single-timeframe fingerprints stay stable. Empty `additional_instruments` and omitted
`entry.pyramiding` are also dropped so existing single-instrument, single-lot fingerprints stay
stable ([ADR 0056](../decisions/0056-multi-instrument-documents-and-pyramiding.md)).

### Field rules

| Field | Type | Rules |
|-------|------|-------|
| `schema_version` | string | Semver. Currently `"1.0"`. Breaking changes bump major. |
| `strategy_id` | UUIDv7 string | Identity of the mutable strategy; stable across every edit and snapshot. The server forces it on save. |
| `name` | string | 1–120 characters. |
| `description` | string | Optional, ≤ 500 characters. |
| `created_at` | RFC 3339 UTC | Set by backend on creation, never edited (the server forces it on save). |
| `instrument` | object | Explicit primary Coinbase `BASE-USD` or `BASE-USDC` spot product, never inherited from runtime. |
| `additional_instruments` | array \| omitted | Optional 1–7 extra unique spot products with the same quote currency as `instrument`, disjoint from `instrument`. Total coverage is at most eight. Omitted from canonical JSON when empty. |
| `timeframe` | enum | One ingested venue clock (`1m`, `5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `1d`). This is the LTF decision clock. Paper and live use the same clock. Sub-hour live requires a connected user-order feed. |
| `data_requirements` | object | Minimum LTF bars and OHLCV fields needed for indicator warmup. |
| `indicators` | array | Named indicator definitions (see below). Optional per-indicator `timeframe`. |
| `htf_filter` | object \| omitted | Optional HTF filter (see below). Omitted from canonical JSON when null. |
| `entry` | object | LTF signal conditions and entry constraints. |
| `sizing` | object | Position-sizing policy. |
| `portfolio_limits` | object | Exposure and concurrency limits. |
| `exits` | object | Stop-loss, take-profit, trailing, time, and optional signal exits. |
| `execution` | object | Maker/taker preference and fill-wait policy. |
| `metadata` | object | Typed operator tags and notes; never affects evaluation. |

### Lifecycle, snapshots, and fingerprint

[ADR 0082](../decisions/0082-strategy-root-mutable-strategies-auto-snapshots.md) replaced the former
`draft` → `published` → `archived` lifecycle and per-identity `version` numbers. The document no
longer has `version` or `status` fields. Input that still carries those legacy keys (older exports)
is accepted and the keys are discarded; they never appear in canonical bytes.

- A strategy is edited and saved in place. A save may hold an invalid document; the stored
  validation result lists `issues` (`loc`, `message`) and `current_fingerprint` is null.
- Backtest, study, and deployment starts require a valid current definition, then snapshot it.
  Identical content deduplicates to one snapshot.
- **Fingerprint** (stable, documented): `strategy_fingerprint = "sha256:" + hex(SHA-256(canonical
  bytes))`. Canonical bytes are the revalidated document serialized as UTF-8 JSON with sorted keys,
  `(",", ":")` separators, and NaN forbidden, omitting null `htf_filter`, empty
  `additional_instruments`, absent `entry.pyramiding`, absent or null `exits.signal_exit`, and
  absent indicator `input` / operand `series`. Every document field is covered, including `strategy_id`, `created_at`, `name`,
  `description`, and `metadata`, so a rename yields a new snapshot. Parameter-sweep variants keep
  the base `strategy_id`.

A snapshot's deterministic canonical JSON and content hash let backtest results, bots, trade
reasons, and audit events prove which exact definition was used.

## Indicators

Indicators are named, typed definitions with stable IDs for referencing in conditions.

```json
{
  "id": "ema_fast",
  "kind": "ema",
  "input": "close",
  "parameters": { "period": 20 }
}
```

Optional `timeframe` on an LTF-list indicator selects a coarser integer-multiple venue clock
([ADR 0042](../decisions/0042-per-indicator-timeframes.md)). Omit the field to use the strategy
decision clock; canonical JSON omits it when absent so existing fingerprints stay stable. `constant`
must omit `timeframe`. HTF-filter indicators must omit `timeframe`. Stop and trailing ATR stay on
the decision clock.

Optional `offset` (integer 0–500) lags every output of one declaration by that many completed
bars of the indicator's own clock, for example the previous bar's 20-bar high
([ADR 0086](../decisions/0086-indicator-catalog-expansion-and-offset.md)):

```json
{ "id": "prior_high", "kind": "highest", "input": "high", "parameters": { "period": 20 }, "offset": 1 }
```

Warmup adds `offset`. `constant` must omit it. `offset: 0` normalizes to omitted, and canonical
JSON omits it, so existing fingerprints stay stable.

An **operand** can independently read an earlier value of the same declaration:
`{"indicator":"bands","series":"upper","offset":1}`. Operand offsets are strict integers
0–500 and lag completed bars on the indicator's own clock before alignment. They add to any
declaration offset. Zero is omitted from canonical JSON; literals reject offsets and constants
reject positive offsets. Warmup includes the largest operand lag for each indicator across entry,
signal-exit, and HTF-filter rules. Missing history is undefined, including under NOT. This lets
parameter sweeps update current and prior reads together without duplicate declarations
([ADR 0099](../decisions/0099-operand-level-indicator-offsets.md)).

### V1 indicator catalog

| Kind | Input | Required parameters | Output | Minimum warmup |
|------|-------|---------------------|--------|----------------|
| `ema` | one of `open`, `high`, `low`, `close`, `volume` | `period` (2–500) | single value per bar | `period` |
| `sma` | one of `open`, `high`, `low`, `close`, `volume` | `period` (2–500) | single value per bar | `period` |
| `rsi` | `close` | `period` (2–100) | 0–100 per bar | `period + 1` |
| `atr` | `high, low, close` | `period` (2–100) | single value per bar | `period` |
| `volume_sma` | `volume` | `period` (2–500) | single value per bar | `period` |
| `highest` | one of `open`, `high`, `low`, `close`, `volume` | `period` (2–500) | single value per bar | `period` |
| `lowest` | one of `open`, `high`, `low`, `close`, `volume` | `period` (2–500) | single value per bar | `period` |
| `stdev` | one of `open`, `high`, `low`, `close`, `volume` | `period` (2–500) | population stdev per bar | `period` |
| `stdev_sample` | one of `open`, `high`, `low`, `close`, `volume` | `period` (2–500) | sample stdev per bar | `period` |
| `roc` | one of `open`, `high`, `low`, `close`, `volume` | `period` (2–500) | single value per bar | `period + 1` |
| `williams_r` | `high, low, close` | `period` (2–100) | single value per bar | `period` |
| `cci` | `high, low, close` | `period` (2–100) | single value per bar | `period` |
| `wma` | one of `open`, `high`, `low`, `close`, `volume` | `period` (2–500) | single value per bar | `period` |
| `momentum` | one of `open`, `high`, `low`, `close`, `volume` | `period` (2–500) | single value per bar | `period + 1` |
| `mfi` | `high, low, close, volume` | `period` (2–100) | 0–100 per bar | `period + 1` |
| `macd` | `close` | `fast_period`, `slow_period`, `signal_period` (each 2–500; fast < slow) | series `macd`, `signal`, `histogram` | `slow_period + signal_period - 1` |
| `bollinger` | `close` | `period` (2–500), `stdev_multiplier` (plain decimal `> 0` and `≤ 10`) | series `middle`, `upper`, `lower` | `period` |
| `stochastic` | `high, low, close` | `k_period` (2–100), `d_period` (2–500) | series `k`, `d` | `k_period + d_period - 1` |
| `adx` | `high, low, close` | `period` (2–100) | series `adx`, `plus_di`, `minus_di` | `2 * period - 1` |
| `identity` | one of `open`, `high`, `low`, `close`, `volume` | `{}` | that candle field | `1` |
| `constant` | omitted | `value` (plain decimal) | that level on every bar | `1` |
| `dema` / `tema` | one OHLCV field | `period` (2–500) | single value | `2 * period - 1` / `3 * period - 2` |
| `hma` | one OHLCV field | `period` (2–500) | Hull MA | `period + floor(sqrt(period)) - 1` |
| `kama` | one OHLCV field | `period` (2–100), `fast_period` (2–100), `slow_period` (3–500; fast < slow) | single value | `period + 1` |
| `vwma` | `close, volume` | `period` (2–500) | single value | `period` |
| `supertrend` | `high, low, close` | `period` (2–100), `multiplier` (`> 0`, `≤ 10`) | series `value`, `direction` (1 up, −1 down) | `period` |
| `parabolic_sar` | `high, low` | `step`, `max_step` (decimals, `0 < step ≤ max_step ≤ 1`) | single value | `2` |
| `aroon` | `high, low` | `period` (2–500) | series `up`, `down`, `oscillator` | `period + 1` |
| `ichimoku` | `high, low` | `tenkan_period` < `kijun_period` < `senkou_b_period` (2–500) | series `tenkan`, `kijun`, `senkou_a`, `senkou_b` (no displacement, no chikou) | longest window |
| `vortex` | `high, low, close` | `period` (2–500) | series `plus`, `minus` | `period + 1` |
| `linear_regression` | one OHLCV field | `period` (2–500) | series `value`, `slope` | `period` |
| `trix` | `close` | `period` (2–500) | single value | `3 * period - 1` |
| `stochastic_rsi` | `close` | `rsi_period`, `stoch_period` (2–100), `k_period`, `d_period` (1–100) | series `k`, `d` | `rsi + stoch + k + d - 2` |
| `ppo` | `close` | `fast_period`, `slow_period`, `signal_period` (as MACD) | series `ppo`, `signal`, `histogram` | `slow_period + signal_period - 1` |
| `ultimate_oscillator` | `high, low, close` | `short_period` < `medium_period` < `long_period` (2–100) | 0–100 | `long_period + 1` |
| `awesome_oscillator` | `high, low` | `fast_period` < `slow_period` (2–500) | single value | `slow_period` |
| `cmo` | `close` | `period` (2–100) | −100 to 100 | `period + 1` |
| `tsi` | `close` | `long_period`, `short_period` (< long), `signal_period` (2–500) | series `tsi`, `signal` | `long + short + signal - 1` |
| `keltner` | `high, low, close` | `period` (2–500), `atr_period` (2–100), `multiplier` (`> 0`, `≤ 10`) | series `upper`, `middle`, `lower` | `max(period, atr_period)` |
| `donchian` | `high, low` | `period` (2–500) | series `upper`, `middle`, `lower` | `period` |
| `bollinger_percent_b` / `bollinger_bandwidth` | `close` | as `bollinger` | single value | `period` |
| `natr` | `high, low, close` | `period` (2–100) | ATR % of close | `period` |
| `choppiness` | `high, low, close` | `period` (2–500) | 0–100 | `period` |
| `historical_volatility` | `close` | `period` (2–500), optional `annualization_periods` (1–525600) | percent | `period + 1` |
| `obv` | `close, volume` | `signal_period` (2–500) | series `obv`, `signal` | `signal_period` |
| `accumulation_distribution` | `high, low, close, volume` | `signal_period` (2–500) | series `ad`, `signal` | `signal_period` |
| `cmf` / `vwap` | `high, low, close, volume` | `period` (2–500) | single value | `period` |
| `force_index` | `close, volume` | `period` (2–500) | single value | `period + 1` |
| `zscore` | one OHLCV field | `period` (2–500) | single value | `period` |
| `percent_rank` | one OHLCV field | `period` (2–500) | 0–100 | `period + 1` |

Rules:

- IDs must be unique within a strategy. Indicator ids cannot contain `.`.
- Single-source `input` must be one of: `open`, `high`, `low`, `close`, `volume`. Configurable
  kinds (`ema`, `sma`, `wma`, `highest`, `lowest`, `stdev`, `stdev_sample`, `roc`, `momentum`,
  `dema`, `tema`, `hma`, `kama`, `linear_regression`, `zscore`, `percent_rank`) accept any one of
  those fields. RSI stays locked to `close`; `volume_sma` to `volume`; `macd`, `bollinger`, `trix`,
  `stochastic_rsi`, `ppo`, `cmo`, `tsi`, `bollinger_percent_b`, `bollinger_bandwidth`, and
  `historical_volatility` to `close`. Tuple inputs use one canonical ordered array per kind:
  `["high", "low", "close"]` (ATR, `williams_r`, `cci`, `stochastic`, `adx`, `supertrend`,
  `vortex`, `ultimate_oscillator`, `keltner`, `natr`, `choppiness`), `["high", "low"]`
  (`parabolic_sar`, `aroon`, `ichimoku`, `awesome_oscillator`, `donchian`), `["close", "volume"]`
  (`vwma`, `obv`, `force_index`), and `["high", "low", "close", "volume"]` (`mfi`, `cmf`,
  `accumulation_distribution`, `vwap`). `identity` selects exactly one single-source field.
  `constant` omits `input` and declares `parameters.value`.
- Parameters are decimal strings for monetary fields, constant levels, band multipliers
  (`stdev_multiplier`, `multiplier`), and Parabolic SAR `step` / `max_step`; integers for periods.
  Every parameter is required except `historical_volatility.annualization_periods`. Ordering rules
  (`fast_period < slow_period`, `tenkan_period < kijun_period < senkou_b_period`,
  `short_period < medium_period < long_period`, TSI `short_period < long_period`,
  `step <= max_step`) fail closed. Identity parameters are the empty object.
- Multi-series kinds (`macd`, `bollinger`, `stochastic`, `adx`, `supertrend`, `aroon`, `ichimoku`,
  `vortex`, `linear_regression`, `stochastic_rsi`, `ppo`, `tsi`, `keltner`, `donchian`, `obv`,
  `accumulation_distribution`) emit named outputs. Conditions must set operand `series` to one of
  that kind's declared names. Single-output operands **must omit** `series`. Canonical JSON omits
  `series` when absent so already-published single-output fingerprints stay stable
  ([ADR 0032](../decisions/0032-phase-9-macd-bollinger.md),
  [ADR 0047](../decisions/0047-wider-fail-closed-indicator-catalog.md)).
- An indicator with insufficient warmup data produces no value (not zero, not an error); conditions
  referencing an undefined value evaluate to no-signal. A zero divisor is undefined unless the
  formula documents a convention ([signal evaluation](signal-evaluation.md)).

No broad TA-library passthrough is allowed. Every supported indicator has a defined specification,
warmup requirement, and invalid-data behavior; the operator `indicators` report lists each kind's
parameters, bounds, builder defaults, outputs, and warmup formula
([ADR 0047](../decisions/0047-wider-fail-closed-indicator-catalog.md),
[ADR 0086](../decisions/0086-indicator-catalog-expansion-and-offset.md)).
Per-indicator timeframes are shipped ([ADR 0042](../decisions/0042-per-indicator-timeframes.md)).

## Conditions

Conditions are recursive declarative groups supporting the nested AND/OR builder from the product
vision.

### Structure

```json
{
  "all": [
    {
      "left": { "indicator": "ema_fast" },
      "operator": "crosses_above",
      "right": { "indicator": "ema_slow" }
    },
    {
      "left": { "indicator": "rsi_14" },
      "operator": "greater_than",
      "right": { "literal": "50" }
    }
  ]
}
```

### Group operators

| Operator | Semantics |
|----------|-----------|
| `all` | AND — every child condition must be true. |
| `any` | OR — at least one child must be true. |
| `not` | Negation of exactly one child condition. |

The implemented grammar permits any group type at the root or beneath another group. `all` and
`any` contain 1–20 children; `not` contains exactly one child. A condition tree is limited to 64
total nodes and depth 4, counting the root group and comparison leaves. JSON object keys are sorted
for canonical serialization, while child-array order is preserved as authored and remains part of
the strategy fingerprint. No condition reordering or boolean-algebra simplification occurs.

### Operands

| Operand type | Example | Description |
|-------------|---------|-------------|
| `indicator` | `{ "indicator": "ema_fast" }` | Single-output kind: references an indicator by `id` and must omit `series`. |
| `indicator` | `{ "indicator": "trend_macd", "series": "histogram" }` | Multi-series kind: `series` is required and must be one of that kind's declared outputs. |
| `literal` | `{ "literal": "50" }` | A decimal-string constant. |

### Comparison operators

| Operator | Description |
|----------|-------------|
| `greater_than` | Left > right. |
| `less_than` | Left < right. |
| `greater_than_or_equal` | Left ≥ right. |
| `less_than_or_equal` | Left ≤ right. |
| `equals` | Left == right. |
| `crosses_above` | Left was ≤ right on previous bar and > right on current bar. Both operands must be indicators. |
| `crosses_below` | Left was ≥ right on previous bar and < right on current bar. Both operands must be indicators. |

### Bar evaluation semantics

- Signals are evaluated **only when a candle closes** (never on incomplete/current bar).
- A crossover compares the last **two completed bars** and requires two indicator operands. Compare
  an indicator to a constant with `greater_than*` / `less_than*` and a `literal`, or declare a
  `constant` kind and cross that id. Copy a candle field with `identity` (for example close vs SMA).
- No indicator value or condition may reference data from a future bar.
- If any required indicator value is undefined (insufficient warmup), the entire condition group
  evaluates to **no signal**, not an error.
- Evaluation failure produces no trade. The engine records a structured diagnostic event.

## Higher-timeframe filter

Optional `htf_filter` is the Phase 8 filter plus paper/live evaluation
([ADR 0025](../decisions/0025-multi-timeframe-htf-filter.md),
[ADR 0041](../decisions/0041-paper-live-htf-filter-evaluation.md)).
It is not a second decision clock.

```json
{
  "timeframe": "1h",
  "data_requirements": {
    "warmup_bars": 50,
    "required_fields": ["open", "high", "low", "close", "volume"]
  },
  "indicators": [
    {"id": "htf_ema_fast", "kind": "ema", "input": "close", "parameters": {"period": 20}},
    {"id": "htf_ema_slow", "kind": "ema", "input": "close", "parameters": {"period": 50}}
  ],
  "when": {
    "all": [
      {
        "left": {"indicator": "htf_ema_fast"},
        "operator": "greater_than",
        "right": {"indicator": "htf_ema_slow"}
      }
    ]
  }
}
```

| Rule | Contract |
|------|----------|
| HTF timeframe | Any ingested venue clock that is strictly coarser than LTF and an integer multiple of LTF duration ([ADR 0040](../decisions/0040-venue-strategy-paper-live-htf-clocks.md)) |
| Indicator ids | Unique within HTF and disjoint from LTF ids |
| `when` references | HTF indicators only; LTF `entry.when` and ATR stop stay on LTF indicators |
| Combined signal | Tri-state AND of HTF `when` and LTF `entry.when` |
| Alignment | At LTF close `T`, use the last HTF bar whose exclusive close is `≤ T`. Never a partial HTF bar. Same-close HTF bars are eligible. |
| Research | `dataset_fingerprint` is LTF; `htf_dataset_fingerprint` is required, distinct, and bound |
| Paper / live | Evaluate last-completed complete-only HTF bars. Do not ignore the filter. Missing HTF coverage pauses. |

Per-indicator extra clocks overlay last-completed values onto the decision-clock row **before**
`entry.when`. The HTF filter remains a separate AND. Extra TFs that are not already
`htf_filter.timeframe` require `indicator_dataset_fingerprints`.

`1m` cannot be HTF (nothing in the catalog is finer). `4h` LTF may use `1d` only (`6h` is not an integer multiple).

## Reference instruments

Optional `data_requirements.reference_instruments` declares up to three **read-only** series from
other instruments ([ADR 0096](../decisions/0096-reference-instruments.md)). Indicators read one
with `source`; operands in `entry.when` and `exits.signal_exit` then reference those indicators
normally. Example: trade an alt only while BTC-USDC's last closed daily close is above its EMA(100).

```json
"data_requirements": {
  "warmup_bars": 50,
  "required_fields": ["open", "high", "low", "close", "volume"],
  "reference_instruments": [{"id": "btc", "product_id": "BTC-USDC", "timeframe": "1d"}]
},
"indicators": [
  {"id": "btc_close", "kind": "identity", "input": "close", "source": "btc", "parameters": {}},
  {"id": "btc_ema", "kind": "ema", "input": "close", "source": "btc", "parameters": {"period": 100}}
]
```

| Rule | Contract |
|------|----------|
| Count and ids | 1-3 entries; `id` matches `^[a-z][a-z0-9_]{0,31}$`, unique; no repeated `product_id` + `timeframe` pair |
| Product | Coinbase spot product in the **strategy's quote currency**. Paper/live additionally require the series on the enabled market-data watchlist (watch-add only accepts enabled spot products) |
| Timeframe | Equal to the strategy timeframe or a coarser integer multiple of it (the HTF pairing rule with the decision clock allowed) |
| `source` | Names a declared reference id; the indicator omits `timeframe` (it reads the reference clock). `constant` takes no source; ATR stop/trail indicators and `htf_filter` indicators never take one |
| Readers | Every reference is read by at least one indicator |
| Warmup | Derived per reference from its indicators (period plus offset); `warmup_bars` covers the traded instrument only |
| Alignment | At decision close `T`, a reference indicator reads the last reference bar whose exclusive close is `≤ T`. An in-progress reference bar is never read; a same-timeframe reference reads the bar that closes at `T` |
| Research | Reference datasets bind like extra clocks (`bound_datasets` role `reference`) or are pinned with `reference_dataset_fingerprints`; omitted bounds shrink to reference coverage |
| Paper / live | Reference bars are loaded every cycle. A missing or stale reference skips entries (`reference_data_missing` / `reference_data_stale`); exits keep running |
| Canonical bytes | An empty or omitted list is dropped from canonical JSON, so reference-free documents keep their bytes and fingerprints; declaring references changes the fingerprint |

Not supported: orders on a reference (no cross-instrument orders, pairs, or spreads), more than one
traded instrument per strategy beyond ADR 0056 covered products (which all share the same
references), a reference in another quote currency, a reference finer than the decision clock, and
HTF-filter indicators on a reference.

## Entry

```json
{
  "side": "long",
  "when": {
    "all": [
      {
        "left": { "indicator": "ema_fast" },
        "operator": "crosses_above",
        "right": { "indicator": "ema_slow" }
      }
    ]
  },
  "cooldown_bars": 3,
  "max_open_positions": 1
}
```

V1 constraints:

- **`side` is `"long"` or `"short"`.** Live Coinbase Advanced Trade stays **spot**: shorts sell
  available base and fail closed when inventory is missing. Margin, leverage, and derivatives stay
  out of scope ([ADR 0045](../decisions/0045-spot-shorting-and-attached-entry-brackets.md)).
- `cooldown_bars` prevents re-entry within N bars of the last exit.
- `max_open_positions` is 1 unless `entry.pyramiding` is `{"enabled": true, "require_unrealized_profit": true}` with `max_open_positions` 2–8 (total fills per product). Averaging down is rejected. Paper/live also require risk-policy `allow_intra_strategy_pyramiding`. Omitted pyramiding keeps existing bytes.

## Sizing

```json
{
  "kind": "risk_fraction",
  "risk_fraction": "0.005",
  "max_quote_notional": "100.00",
  "min_quote_notional": "10.00"
}
```

### V1 sizing policies

| Kind | Parameters | Description |
|------|-----------|-------------|
| `fixed_quote` | `amount` | Fixed quote-currency amount per entry. |
| `risk_fraction` | `risk_fraction` | Position size derived from entry-to-stop distance. `risk_fraction` is a fraction of total portfolio value (greater than 0 and at most 0.25). |

Rules:

- `max_quote_notional` and `min_quote_notional` bound the computed size.
- Sizing rounds **down** against product increments and must reject orders below exchange minimums.
- Never silently resize upward.
- All monetary values are decimal strings.

## Portfolio limits

```json
{
  "max_strategy_exposure_fraction": "0.10",
  "max_concurrent_positions": 1
}
```

These are separate from sizing to allow risk policies to override or constrain strategy intent.
`max_concurrent_positions` is 1–8 and must not exceed the number of covered products. It caps
distinct **product** books inside one document. Pyramid adds on an open book do not consume an extra
slot.

## Exits

```json
{
  "initial_stop": {
    "kind": "atr_multiple",
    "atr_indicator": "atr_14",
    "multiple": "2.0"
  },
  "take_profit": {
    "kind": "reward_risk",
    "multiple": "2.0"
  },
  "trailing_stop": {
    "enabled": false
  },
  "time_exit": {
    "max_bars_held": 96
  }
}
```

Disabled trailing is only `{"enabled": false}` so existing fingerprints stay stable. Enabled ATR
trailing is `{"enabled": true, "kind": "atr_multiple", "atr_indicator": "atr", "multiple": "1.5"}`
with the same multiple bounds as the initial stop. The named indicator must be an LTF ATR.

### Signal exit

Optional `signal_exit` ([ADR 0093](../decisions/0093-signal-based-exits.md)) closes an open
position when a condition tree matches on a closed bar:

```json
"signal_exit": {
  "when": {
    "all": [
      {"left": {"indicator": "fast"}, "operator": "crosses_below", "right": {"indicator": "slow"}}
    ]
  }
}
```

| Rule | Contract |
|------|----------|
| Grammar | Same as `entry.when`: `all`/`any`/`not`, 1–20 children, depth 4, 64 nodes, crossovers between two indicators |
| References | Decision-list indicators only (per-indicator `timeframe`/`offset` allowed); multi-series operands name a declared `series`; HTF-filter ids are rejected (the filter gates entries only) |
| Evaluation | Every closed bar after the fill bar while a position is open; never on the fill bar |
| Fill | Taker at that bar's close (fee, slippage, half the spread stress), like the time exit; live cancels protection, then sends a marketable cover |
| Precedence | A stop or take-profit the bar touched wins; the signal exit precedes a time exit due on the same close |
| Identity | Omitted from canonical JSON when absent or `null`, so older documents keep their fingerprints |

The initial stop stays mandatory and keeps protecting the position while the rule waits. The
trailing stop, take-profit, and time exit still apply; the first to trigger closes the position.

### Initial stop kinds

| Kind | Parameters | Description |
|------|-----------|-------------|
| `atr_multiple` | `atr_indicator`, `multiple` (0.5–10.0) | Stop at entry ± (ATR × multiple). |
| `percentage` | `percentage` (0.001–0.20) | Stop at entry ± (entry × percentage). |

### Take-profit kinds

| Kind | Parameters | Description |
|------|-----------|-------------|
| `reward_risk` | `multiple` (0.5–10.0) | Target = entry ± (stop_distance × multiple). |
| `none` | — | No take-profit: exit on the stop, the optional ATR trail, or the time exit. Canonical JSON is only `{"kind": "none"}` ([ADR 0090](../decisions/0090-research-correctness-optional-take-profit-diagnostics.md)). Paper rests no TP order; live rests a venue stop-limit at the stop. |
| `percentage` | `percentage` (0.001–0.50) | Target = entry ± (entry × percentage). |

A short's reward/risk target is `entry − stop_distance × multiple`; when that is at or below zero
the entry is skipped with `target_not_positive` (never silently), and strategy responses warn at
save time (`short_target_may_be_non_positive`) when plausible volatility can reach that point
(a stressed ATR of 20% of price per day, scaled by the square root of the bar length).

### Design distinction

The schema distinguishes four concepts that must not be conflated:

1. **Signal intent** — the condition that produces an exit desire.
2. **Exit policy** — the configured stop/target definition in the schema.
3. **Venue order type** — how the broker later implements it (limit, stop-limit, market).
4. **Emergency exit** — risk-governed marketable exit, separate from maker preference.

A schema declaring a stop is not proof a venue-native stop exists or guarantees execution.

## Execution preferences

```json
{
  "entry_preference": "maker_only",
  "max_entry_wait_bars": 2,
  "on_unfilled_entry": "cancel"
}
```

| Field | Allowed values | Default |
|-------|---------------|---------|
| `entry_preference` | `maker_only` | `maker_only` |
| `max_entry_wait_bars` | 1–50 | 2 |
| `on_unfilled_entry` | `cancel`, `reprice` | `cancel` |

Entries are post-only maker limits in backtest, paper, and live. `marketable_limit` is retired: it
was accepted but never honored (every runtime rested a post-only entry). The model still parses it
so persisted snapshots keep verifying byte-for-byte, but the strategy library stores a document
that uses it as invalid (issue at `execution.entry_preference`), so it cannot be snapshotted,
backtested, or deployed until it is changed to `maker_only`.

Emergency exits are governed by risk policy, not execution preference. They may be taker/marketable
when capital protection requires it.

## Validation layers

### 1. Structural validation

- JSON/schema shape conforms to the Pydantic model.
- Required fields present.
- Enum values within allowed sets.
- Decimal strings parse correctly.
- No unknown fields (reject, don't ignore).

### 2. Semantic validation

- Indicator IDs are unique.
- All indicator references in conditions resolve to defined indicators.
- Multi-series operands name a declared output; single-output operands omit `series`.
- Indicator periods are positive and within bounds.
- `warmup_bars` satisfies all indicator minimum warmup requirements.
- Entry `when` references only defined LTF indicators.
- `exits.signal_exit.when` references only defined decision-list indicators (never HTF-filter
  ids), with the entry operand and series rules.
- When `htf_filter` is present: HTF timeframe is a coarser integer multiple of LTF; HTF ids are unique and disjoint; HTF `when` references only HTF indicators; HTF warmup covers HTF indicators.
- Exit `atr_indicator` references a defined ATR indicator.
- Sizing and risk values are within allowed ranges.
- `max_open_positions` is 1 unless pyramiding is enabled; `max_concurrent_positions` is 1–8 and must not exceed covered products.
- Product and timeframe are supported.

### 3. Runtime validation (paper and live)

Paper and live already gate these before new risk-increasing orders. They are not deferred work:

- Dataset completeness and gap check (complete-only publication; missing latest bars pause).
- Current market data is fresh (stale-data cutoff blocks new risk-increasing orders).
- Required credentials, balances, and venue minimums are met (live shorts fail closed without
  available base).
- Live-arm and risk-policy approval exist (Phase 5 arming; Phase 10 registry before intent persist).

This is not a substitute for the shipped risk-policy circuit breakers (daily-loss / drawdown,
order-rate limits, reference-price collars) on paper/live entries
([ADR 0050](../decisions/0050-daily-loss-drawdown-rate-collars.md)).

### Separation from optimization

Parameter sweeps, grid search, and auto-tuning are **not** part of strategy authoring in V1. They
are a separate research activity that can manufacture overfit results. The schema represents one
fixed, human-chosen parameter set. Research studies may select among snapshots of named strategies or
derived variant snapshots ([ADR 0044](../decisions/0044-parameter-sweeps-wfo-stitched-equity.md)) without
rewriting that authoring boundary.

## Reference strategy

The initial end-to-end test vehicle:

- **Instrument:** `BTC-USD`
- **Timeframe:** `1h`
- **Entry:** EMA fast crosses above EMA slow, RSI > 50
- **Indicators:** EMA(20), EMA(50), RSI(14), ATR(14)
- **Initial stop:** ATR × 2.0
- **Take profit:** reward/risk 2.0
- **Sizing:** risk_fraction 0.5%, max $100 quote
- **Max positions:** 1
- **Cooldown:** 3 bars after exit
- **Execution:** maker_only, cancel after 2 bars
- **Optional pyramiding** (same-side adds with required unrealized profit; no averaging down)

### Required research protocol before any "profitable" label

1. Development period backtest (in-sample).
2. Untouched out-of-sample period.
3. Walk-forward validation.
4. Realistic Coinbase fee model (maker/taker).
5. Conservative spread/slippage assumptions.
6. Sensitivity analysis around key parameters.
7. Market-regime breakdown (trending vs ranging).
8. Maximum drawdown and loss-streak analysis.
9. Paper-trading period before live consideration.

A backtest that looks profitable is not evidence of profit. It is evidence that the strategy
survived a historical simulation with stated assumptions. Overfitting is the default, not the
exception.
