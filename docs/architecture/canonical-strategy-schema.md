# Canonical Strategy Schema

> **Status: Partially implemented V1 contract.** The conservative reference profile described
> below now has backend validation, immutable PostgreSQL publication, verified fingerprint loading,
> and exact binding to a verified immutable dataset fingerprint. This creates no order authority.
> The broader authoring contract remains proposed until the unsupported variants below are built.

This document is the implementation-facing specification referenced by
[ADR 0005](../decisions/0005-canonical-strategy-schema.md). The ADR records the decision; this
document defines the contract.

## Current implementation boundary

The first Phase 2B slice implements a deliberately narrow, fail-closed profile:

- frozen models with unknown-field rejection, UUIDv7 identity, UTC timestamps, string-only finite
  decimals normalized to plain canonical text, bounded values, unique indicator IDs, reference
  resolution, and warmup validation;
- 1h Coinbase USD spot, long only, one position, with EMA/RSI/ATR indicators;
- a bounded `all` group of typed comparisons, risk-fraction sizing, ATR-multiple initial stop,
  reward/risk take profit, disabled trailing stops, and conservative maker preferences;
- canonical sorted compact JSON and `sha256:<hex>` identity over the entire published document;
- immutable `published_strategy_versions` rows and exact `strategy_dataset_bindings` rows; creation and
  loading re-verify both artifacts and require Coinbase provider, product, and timeframe compatibility.

The dataset root is a private, worker-owned local trust boundary. Verification and binding have a
bounded verify-then-persist TOCTOU window under that assumption. A binding row records an accepted
association, not permanent consumability; every binding load re-verifies both exact artifacts.

Not yet implemented: SMA/volume-SMA, nested AND/OR/NOT groups, other sizing/stop/trailing variants,
draft persistence and lifecycle transitions, authoring API/UI, summaries, evaluation, backtesting,
paper execution, or live execution. Unsupported shapes are rejected rather than approximated.

## Design principles

1. **One schema, every runtime.** Backtest, paper, and live consume the same immutable version.
2. **Declarative, not executable.** No Python, JavaScript, arbitrary expressions, or UI layout data.
3. **Decimal-precise.** All monetary and quantity values are strings, consistent with existing
   ThyTrader financial boundaries.
4. **Explicit and bounded.** Every field has a type, allowed range, and defined invalid behavior.
5. **Reproducible.** A strategy version + dataset fingerprint + engine version must fully determine
   a backtest result.
6. **No false authority.** A validated strategy is not a profitable strategy and not a live order.

## Top-level document

```json
{
  "schema_version": "1.0",
  "strategy_id": "01978a3e-5f2c-7d10-b3a4-000000000001",
  "version": 1,
  "name": "EMA trend reference",
  "description": "Optional operator-facing description.",
  "status": "draft",
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

### Field rules

| Field | Type | Rules |
|-------|------|-------|
| `schema_version` | string | Semver. Currently `"1.0"`. Breaking changes bump major. |
| `strategy_id` | UUIDv7 string | Stable across all versions of one strategy. |
| `version` | integer ≥ 1 | Monotonically incremented per strategy_id. |
| `name` | string | 1–120 characters. |
| `description` | string | Optional, ≤ 500 characters. |
| `status` | enum | `draft` → `published` → `archived`. See lifecycle below. |
| `created_at` | RFC 3339 UTC | Set by backend on creation, never edited. |
| `instrument` | object | Explicit product, never inherited from runtime. |
| `timeframe` | enum | One of the supported candle intervals. |
| `data_requirements` | object | Minimum bars and OHLCV fields needed for indicator warmup. |
| `indicators` | array | Named indicator definitions (see below). |
| `entry` | object | Signal conditions and entry constraints. |
| `sizing` | object | Position-sizing policy. |
| `portfolio_limits` | object | Exposure and concurrency limits. |
| `exits` | object | Stop-loss, take-profit, trailing, and time exits. |
| `execution` | object | Maker/taker preference and fill-wait policy. |
| `metadata` | object | Typed operator tags and notes; never affects evaluation. |

### Version lifecycle

- **`draft`**: editable. Can be validated but not referenced by backtests or sessions.
- **`published`**: immutable. Backtests, paper sessions, and live decisions reference this version.
  Editing any behaviorally relevant field creates a new version with incremented `version` number.
- **`archived`**: immutable, hidden from active selection. Historical references remain valid.

A published version must have a deterministic canonical JSON serialization and content hash so that
backtest results and audit events can prove which exact definition was used.

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

### V1 indicator catalog

| Kind | Input | Required parameters | Output | Minimum warmup |
|------|-------|---------------------|--------|----------------|
| `ema` | `close` | `period` (2–500) | single value per bar | `period` |
| `sma` | `close` | `period` (2–500) | single value per bar | `period` |
| `rsi` | `close` | `period` (2–100) | 0–100 per bar | `period + 1` |
| `atr` | `high, low, close` | `period` (2–100) | single value per bar | `period` |
| `volume_sma` | `volume` | `period` (2–500) | single value per bar | `period` |

Rules:

- IDs must be unique within a strategy.
- Single-source `input` must be one of: `open`, `high`, `low`, `close`, `volume`. ATR uses the
  canonical ordered array `["high", "low", "close"]` because all three fields are required.
- Parameters are decimal strings for monetary fields, integers for periods.
- An indicator with insufficient warmup data produces no value (not zero, not an error); conditions
  referencing an undefined value evaluate to no-signal.

No broad TA-library passthrough is allowed. Every supported indicator has a defined specification,
warmup requirement, and invalid-data behavior.

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
| `not` | Negation of a single child condition (optional in V1). |

### Operands

| Operand type | Example | Description |
|-------------|---------|-------------|
| `indicator` | `{ "indicator": "ema_fast" }` | References an indicator by its `id`. |
| `literal` | `{ "literal": "50" }` | A decimal-string constant. |

### Comparison operators

| Operator | Description |
|----------|-------------|
| `greater_than` | Left > right. |
| `less_than` | Left < right. |
| `greater_than_or_equal` | Left ≥ right. |
| `less_than_or_equal` | Left ≤ right. |
| `equals` | Left == right. |
| `crosses_above` | Left was ≤ right on previous bar and > right on current bar. |
| `crosses_below` | Left was ≥ right on previous bar and < right on current bar. |

### Bar evaluation semantics

- Signals are evaluated **only when a candle closes** (never on incomplete/current bar).
- A crossover compares the last **two completed bars**.
- No indicator value or condition may reference data from a future bar.
- If any required indicator value is undefined (insufficient warmup), the entire condition group
  evaluates to **no signal**, not an error.
- Evaluation failure produces no trade. The engine records a structured diagnostic event.

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

- **`side` must be `"long"`.** Spot Coinbase is long-only. Short, margin, leverage, and derivatives
  are explicitly out of scope.
- `cooldown_bars` prevents re-entry within N bars of the last exit.
- `max_open_positions` must be 1 in V1. Pyramiding, averaging down, and martingale are rejected.

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
In V1, `max_concurrent_positions` must be 1.

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

### Initial stop kinds

| Kind | Parameters | Description |
|------|-----------|-------------|
| `atr_multiple` | `atr_indicator`, `multiple` (0.5–10.0) | Stop at entry ± (ATR × multiple). |
| `percentage` | `percentage` (0.001–0.20) | Stop at entry ± (entry × percentage). |

### Take-profit kinds

| Kind | Parameters | Description |
|------|-----------|-------------|
| `reward_risk` | `multiple` (0.5–10.0) | Target = entry ± (stop_distance × multiple). |
| `percentage` | `percentage` (0.001–0.50) | Target = entry ± (entry × percentage). |

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
| `entry_preference` | `maker_only`, `marketable_limit` | `maker_only` |
| `max_entry_wait_bars` | 1–50 | 2 |
| `on_unfilled_entry` | `cancel`, `reprice` | `cancel` |

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
- Indicator periods are positive and within bounds.
- `warmup_bars` satisfies all indicator minimum warmup requirements.
- Entry `when` references only defined indicators.
- Exit `atr_indicator` references a defined ATR indicator.
- Sizing and risk values are within allowed ranges.
- `max_open_positions` and `max_concurrent_positions` are both 1 in V1.
- Product and timeframe are supported.

### 3. Runtime validation (deferred to Phase 3+)

- Dataset completeness and gap check.
- Product is currently tradable on the venue.
- Current market data is fresh.
- Required balances and venue minimums are met.
- Live-arm/risk policy approval exists (Phase 5).

### Separation from optimization

Parameter sweeps, grid search, and auto-tuning are **not** part of strategy authoring in V1. They
are a separate research activity that can manufacture overfit results. The schema represents one
fixed, human-chosen parameter set.

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
- **No shorts, no pyramiding, no averaging down**

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
