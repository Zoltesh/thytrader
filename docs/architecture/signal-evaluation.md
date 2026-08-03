# Deterministic Signal Evaluation

## Purpose and boundary

The first executable research engine consumes one exact published research-run specification using
`thytrader-bar-signal-v1`. It reloads and reverifies the published run, published strategy, immutable
dataset manifest, and Parquet candles before calculating indicators or conditions. The evaluator
reconstructs and revalidates typed run and strategy inputs before use, and both canonical identity
helpers do the same before hashing, so unchecked model copies cannot enter evaluation identity.

The output is an in-memory, immutable **entry-condition trace**. A `matched` record means only that the
declarative entry condition matched after one completed candle close. It is not an order intent,
cooldown-approved entry, fill, position, trade, or claim that a full backtest ran.

There is no broker, order submission, REST endpoint, dashboard control, paper execution, or live execution in the signal evaluator itself. It evaluates `thytrader-bar-signal-v1` and the signal stage of `thytrader-bar-backtest-v1`; only the latter is eligible for the separate [bar-level backtest simulator](backtest-simulation.md), which owns position state, modeled fees/slippage, PnL, result persistence, and its own documented execution assumptions.

## Engine contract compatibility

Research-run schema `1.0` accepts three explicit engine-contract identifiers:

- `thytrader-bar-v1` remains the historical request-only contract. The evaluator rejects it, so an
  already-published immutable request never silently acquires executable semantics.
- `thytrader-bar-signal-v1` selects only the deterministic indicator and entry-condition semantics in
  this document; it is not executable by the backtest simulator.
- `thytrader-bar-backtest-v1` selects the same deterministic signal stage plus the separately versioned
  V1 fill, PnL, and persistence policy in [bar-level backtest simulation](backtest-simulation.md).
- `thytrader-bar-backtest-v2` selects that same signal stage plus the V2 constant-spread stress model.
  It requires immutable broker assumptions in the run; signal evaluation still has no broker authority.

The engine identifier is part of canonical run identity. Selecting an executable contract therefore
creates a different run fingerprint even when every other request field is unchanged.

## Candle and evaluation rules

The evaluator selects exactly the contiguous hourly candles in
`[warmup.starts_at, evaluation.ends_at)`. Warmup candles advance indicator state but emit no trace
records. Each candle in `[evaluation.starts_at, evaluation.ends_at)` emits exactly one trace record.
The extra candle required by run publication for a possible next-open fill is never supplied to the
indicator or condition calculation.

Before calculation, every selected candle must:

- occur at its exact expected UTC one-hour boundary;
- have finite Decimal OHLCV values with at most 64 significant digits and adjusted exponent in
  `[-6143, 6144]`;
- have strictly positive open, high, low, and close values;
- satisfy `low <= open <= high` and `low <= close <= high`; and
- have non-negative volume.

Missing, duplicate, reordered, malformed, or non-contiguous input fails closed. The run's strategy
fingerprint and exact strategy-derived warmup must also match before calculation.

## Decimal policy

All indicator arithmetic uses the private `decimal64-half-even-v1` context: precision `64`,
`ROUND_HALF_EVEN`, exponent bounds `Emin=-6143` and `Emax=6144`, and traps for invalid operations,
division by zero, and overflow. Input values must have adjusted exponents in `[-6143, 6144]`.
Calculated values may use the context's subnormal range down to `Etiny=-6206`; trace values must be
exactly representable by that context and therefore cannot carry a stored exponent below `-6206`.
Ambient process Decimal settings cannot affect output. Trace values use plain non-exponent decimal
strings with insignificant trailing zeros removed; negative zero is rendered as `0`.

This is distinct from exchange quantity and price quantization, which belongs to the future broker
boundary. The research evaluator does not create exchange-domain amounts.

## Indicator semantics

All declared indicators are calculated sequentially in strategy declaration order. Every rolling
or seed sum accumulates observations chronologically from oldest to newest using a left fold that
starts at exact Decimal zero. Reassociation, pairwise summation, reverse summation, and provider
library aggregation are incompatible with this engine contract.

### SMA and volume SMA

The first value is defined after exactly `period` observations. Each value is the arithmetic mean of
the current observation and previous `period - 1` observations. SMA consumes close; volume SMA
consumes volume.

### EMA

EMA consumes close. The first value is the arithmetic mean of the first `period` closes. Later values
use:

`ema = ((period - 1) * previous_ema + 2 * close) / (period + 1)`

### ATR

ATR uses Wilder smoothing. The first candle's true range is `high - low`. Later true range is the
maximum of `high - low`, `abs(high - previous_close)`, and `abs(low - previous_close)`. The first ATR
is the arithmetic mean of the first `period` true ranges. Later values use:

`atr = (previous_atr * (period - 1) + true_range) / period`

### RSI

RSI consumes close and requires `period` completed price changes, so its first value occurs after
`period + 1` closes. Initial average gain and loss are the arithmetic means of those first `period`
changes. Later averages use these exact ordered Wilder recurrences:

`average_gain = (previous_average_gain * (period - 1) + current_gain) / period`

`average_loss = (previous_average_loss * (period - 1) + current_loss) / period`

The multiplication and addition complete before division; algebraic reassociation is incompatible
with this engine contract.

- zero average loss with positive average gain produces `100`;
- zero average gain and zero average loss produces neutral `50`; and
- otherwise RSI is `100 * average_gain / (average_gain + average_loss)`.

## Condition semantics

Indicator and exact literal operands support greater-than, greater-than-or-equal, less-than,
less-than-or-equal, and equality comparisons. Recursive `all`, `any`, and `not` nodes use the bounded
canonical strategy tree.

Crosses-above is true only when `previous_left <= previous_right` and `current_left > current_right`.
Crosses-below is symmetric: `previous_left >= previous_right` and `current_left < current_right`.
Only the previous and current completed-candle values participate.

Condition evaluation is tri-state. If a required indicator or prior crossover value is undefined, the
record is `undefined`; `not` must not turn undefined into true, and groups must not hide undefined via
short-circuiting. Valid published warmup should normally make all evaluation-window values defined.

## Trace identity

Each trace records:

- schema and executable engine-contract version;
- exact run, strategy, and dataset fingerprints;
- the identity-bearing exact indicator-ID sequence copied from the published strategy;
- one unique, strictly increasing record per evaluation candle;
- every declared indicator's canonical value or explicit `null` exactly once in that sequence; and
- the final entry-condition outcome: `matched`, `not_matched`, or `undefined`.

Canonical trace JSON uses sorted keys, compact separators, UTF-8, and UTC `Z` timestamps. The complete
trace is addressed by SHA-256. Trace fields are frozen, unknown-field rejecting, and strict about
identity-bearing native strings. A trace contains at least one record; every record contains a
non-empty indicator vector matching the trace's unique declared indicator-ID sequence exactly, with
no missing, extra, duplicate, or reordered entries. Traces are not yet persisted; rerunning the exact
publication recreates the same canonical bytes. Canonical serialization revalidates the complete
typed trace before emitting bytes so unchecked model copies cannot acquire fingerprints.

## Read-only operator command

An operator with an existing published `thytrader-bar-signal-v1` run can evaluate it with:

```bash
uv run thytrader-research-evaluate <run_fingerprint> --pretty
```

The command reads `THYTRADER_DATABASE_URL` and `THYTRADER_MARKET_DATA_DATASET_ROOT`, prints trace JSON
to standard output, and prints `trace_fingerprint=...` to standard error. It cannot publish or mutate
runs, strategies, datasets, results, or trading state. Failure output is deliberately generic and does
not expose database URLs, artifact content, or credentials.
