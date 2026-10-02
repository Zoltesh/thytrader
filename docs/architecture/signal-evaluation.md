# Deterministic Signal Evaluation

## Purpose and boundary

The signal stage of the single backtest model (`engine: "thytrader-backtest"`,
[ADR 0083](../decisions/0083-unified-backtest-model.md)) consumes one exact published research-run
specification. It reloads and reverifies the published run, strategy snapshot, immutable
dataset manifest, and Parquet candles before calculating indicators or conditions. The evaluator
reconstructs and revalidates typed run and strategy inputs before use, and both canonical identity
helpers do the same before hashing, so unchecked model copies cannot enter evaluation identity.

The output is an in-memory, immutable **entry-condition trace**. A `matched` record means only that the
declarative entry condition matched after one completed candle close. It is not an order intent,
cooldown-approved entry, fill, position, trade, or claim that a full backtest ran.

There is no broker, order submission, REST endpoint, dashboard control, paper execution, or live execution in the signal evaluator itself. It evaluates the signal stage of every `thytrader-backtest` run; the separate [bar-level backtest simulator](backtest-simulation.md) owns position state, modeled fees/slippage, PnL, result persistence, and its own documented execution assumptions.

## Engine identity

Research-run schema `1.0` accepts exactly one engine identifier, `thytrader-backtest`. The evaluator
rejects any other value, and signal evaluation has no broker or order authority. Signal traces carry
`engine` so a trace is bound to the model that consumes it.

## Candle and evaluation rules

The evaluator selects exactly the contiguous decision-timeframe candles in
`[warmup.starts_at, evaluation.ends_at)`. Warmup candles advance indicator state but emit no trace
records. Each candle in `[evaluation.starts_at, evaluation.ends_at)` emits exactly one trace record.
The extra candle required by run publication for end-of-window liquidation is never supplied to the
indicator or condition calculation. When `htf_filter` is present, HTF indicators are calculated on
the required closed HTF bars and held onto each LTF close from the last completed HTF bar (never a
partial HTF bar). Combined entry is the tri-state AND of HTF `when` and LTF `entry.when`. Paper and
live share that last-completed alignment on complete-only HTF candles
([ADR 0041](../decisions/0041-paper-live-htf-filter-evaluation.md)). Extra-TF LTF-list indicators
are calculated on last-completed bars of those clocks and overlay the decision-clock row before
`entry.when` ([ADR 0042](../decisions/0042-per-indicator-timeframes.md)).

Before calculation, every selected candle must:

- occur at its exact expected UTC decision-timeframe candle boundary;
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
strings with an optional leading minus and insignificant trailing zeros removed; negative zero is
rendered as `0`.

This is distinct from exchange quantity and price quantization, which belongs to the future broker
boundary. The research evaluator does not create exchange-domain amounts.

## Indicator semantics

All declared indicators are calculated sequentially in strategy declaration order. Every rolling
or seed sum accumulates observations chronologically from oldest to newest using a left fold that
starts at exact Decimal zero. Reassociation, pairwise summation, reverse summation, and provider
library aggregation are incompatible with this engine contract.

### SMA and volume SMA

The first value is defined after exactly `period` observations. Each value is the arithmetic mean of
the current observation and previous `period - 1` observations. SMA consumes one author-selected
OHLCV field (`open`, `high`, `low`, `close`, or `volume`). Volume SMA remains locked to volume.

### Highest and lowest

`highest` and `lowest` consume one author-selected OHLCV field. The first value is defined after
exactly `period` observations. Each value is the maximum or minimum of the current observation and
previous `period - 1` observations, scanned oldest to newest. The window includes the current
completed bar and never a future bar.

### Stdev

`stdev` consumes one author-selected OHLCV field. The first value is defined after exactly `period`
observations. Each value is the **population** standard deviation of the inclusive window:

1. `mean` is the chronological left-fold sum of the window divided by `period`;
2. `variance` is the chronological left-fold sum of `(x - mean)^2` divided by `period`;
3. `stdev` is the engine-context square root of that variance.

A non-positive variance after that fold yields `0` (flat windows are defined, not undefined). This
is not sample stdev (`N-1`) and not a TA-library `stdev`.

### Sample stdev

`stdev_sample` uses the same left-fold mean and squared-deviation sum as `stdev`, then divides by
`period - 1` (`period >= 2`). Non-positive variance yields `0`. Extra `ddof` on either kind fails
closed. This is not a TA-library `stdev`.

### ROC

`roc` consumes one author-selected OHLCV field. The first value is defined after `period + 1`
observations because the lookback is exactly `period` completed bars ago. Each defined value is:

`roc = 100 * (value - value[period]) / value[period]`

A lookback value of `0` yields undefined, not infinity. This is not a TA-library `roc`.

### Williams %R

`williams_r` consumes high, low, and close. The first value is defined after exactly `period`
observations. The inclusive window reuses the shipped rolling max/min left-folds. Each defined value
divides `(highest_high - close)` by `(highest_high - lowest_low)`, then multiplies by `-100`. A zero
window range yields undefined, not `0`.

### CCI

`cci` consumes high, low, and close. Typical price is `(high + low + close) / 3`. The first value is
defined after exactly `period` typical prices. Each defined value uses the shipped SMA left-fold of
typical price and a population mean absolute deviation (chronological `abs(TP - SMA)` sum divided by
`period`). Then:

`cci = (TP - SMA(TP)) / (0.015 * MAD)`

The Lambert product completes before the divide. A zero MAD yields undefined, not infinity. This is
not a TA-library `cci`.

### WMA

`wma` consumes one author-selected OHLCV field. The first value is defined after exactly `period`
observations. Oldest weight is `1` and newest weight is `period`. Each value left-folds
`value * weight`, left-folds the weights, then divides:

`wma = sum(source[i] * (i + 1)) / (period * (period + 1) / 2)`

This is not a TA-library `wma`.

### Momentum

`momentum` consumes one author-selected OHLCV field. The first value is defined after `period + 1`
observations because the lookback is exactly `period` completed bars ago, matching ROC. Each defined
value is:

`momentum = value - value[period]`

A zero lookback close is a defined difference. This is not a TA-library `mom`.

### MFI

`mfi` consumes high, low, close, and volume. Typical price is `(high + low + close) / 3`. Raw money
flow is `TP * volume`. The first value is defined after `period + 1` bars because each of the last
`period` bars is signed against the immediately previous typical price: greater adds to positive
money flow, less adds to negative, equal adds to neither. Each defined value is:

`mfi = 100 * positive / (positive + negative)`

The sum completes before the divide. A zero total yields undefined, not `50` or `100`. An all-positive
window is `100`; an all-negative window is `0`. This is not a TA-library `mfi`.

### Identity

`identity` copies one completed-bar OHLCV field: `open`, `high`, `low`, `close`, or `volume`. The
first value is defined on the first supplied bar. Parameters are the empty object. This is not a
rolling window. Rolling kinds select their own source independently
([ADR 0047](../decisions/0047-wider-fail-closed-indicator-catalog.md)).

### Constant

`constant` omits `input`. `parameters.value` is a finite plain decimal string using the same
canonicalization as condition literals. Every completed bar repeats that exact value. The first
value is defined on the first supplied bar.

### MACD

`macd` consumes close. Parameters are `fast_period`, `slow_period`, and `signal_period` (each 2–500,
fast strictly less than slow). Outputs are three named series: `macd`, `signal`, and `histogram`.
The MACD line is defined after `slow_period` closes:

`macd = EMA(close, fast_period) - EMA(close, slow_period)`

Each EMA is the shipped SMA-seeded recurrence. `signal` is that same recurrence applied to the
suffix of defined MACD-line values with period `signal_period`. `histogram` is `macd - signal`.
Signal and histogram are first defined after `slow_period + signal_period - 1` bars. A difference
is undefined until both operands are defined. This is not a TA-library `macd`.

Trace and evaluator keys are `{id}.macd`, `{id}.signal`, and `{id}.histogram`. Conditions must name
one of those series.

### Bollinger

`bollinger` consumes close. Parameters are `period` (2–500) and `stdev_multiplier` (plain decimal
`> 0` and `≤ 10`). Outputs are three named series: `middle`, `upper`, and `lower`. The first values
are defined after exactly `period` closes. `middle` is the shipped SMA of close. Band width uses
the shipped population `stdev` of the same inclusive window, not sample / `N-1`:

`upper = middle + stdev_multiplier * stdev`

`lower = middle - stdev_multiplier * stdev`

A zero stdev yields equal bands at the middle (defined). This is not a TA-library `bbands`.

Trace and evaluator keys are `{id}.middle`, `{id}.upper`, and `{id}.lower`. Conditions must name
one of those series.

### Stochastic

`stochastic` consumes high, low, and close. Parameters are `k_period` (2–100) and `d_period`
(2–500). Outputs are two named series: `k` and `d`. `%K` is defined after `k_period` bars:

`k = 100 * (close - lowest_low) / (highest_high - lowest_low)`

The inclusive window reuses the shipped rolling max/min left-folds. A zero window range yields
undefined `%K`. `%D` is the shipped SMA of the last `d_period` `%K` values. Any undefined `%K` in
that window yields undefined `%D`. `%D` is first defined after `k_period + d_period - 1` bars. This
is fast stochastic (no extra slowing period) and not a TA-library `stoch`.

Trace and evaluator keys are `{id}.k` and `{id}.d`. Conditions must name one of those series.

### ADX

`adx` consumes high, low, and close. Parameter `period` is 2–100. Outputs are three named series:
`adx`, `plus_di`, and `minus_di`. True range matches shipped ATR. First-bar `+DM` and `-DM` are `0`.
Later `+DM` is the up-move when it is strictly greater than the down-move and positive; `-DM` is the
down-move when it is strictly greater than the up-move and positive; ties are both `0`. Smoothed TR,
`+DM`, and `-DM` use the shipped ATR Wilder seed and recurrence. `plus_di` /
`minus_di` are `100 * smoothed_DM / smoothed_TR` and are first defined after `period` bars. A zero
smoothed TR yields undefined DI. `DX` is `100 * abs(plus_di - minus_di) / (plus_di + minus_di)`. A
zero DI sum yields undefined `DX`. `ADX` is that same Wilder smooth of defined `DX` values and is
first defined after `2 * period - 1` bars when every DX after DI warmup is defined. This is not a
TA-library `adx`.

Trace and evaluator keys are `{id}.adx`, `{id}.plus_di`, and `{id}.minus_di`. Conditions must name
one of those series.

### Indicator bar lag (`offset`)

Any declaration except `constant` may set `offset` (integer 0–500,
[ADR 0086](../decisions/0086-indicator-catalog-expansion-and-offset.md)). Every output of that
declaration on bar `t` is the unlagged value from bar `t - offset` of the indicator's own clock
(the decision clock, an extra per-indicator timeframe, or the HTF filter clock). The first
`offset` bars are undefined and nothing later than bar `t` is read. Warmup is the base warmup plus
`offset`. `offset: 0` normalizes to omitted and canonical JSON omits it. Trace keys do not change.

### Wider catalog (ADR 0086)

Shared rules for the kinds below: every window includes the current completed bar unless stated;
sums are oldest-first left folds from exact zero; an EMA is the shipped SMA-seeded recurrence
`((period - 1) * previous + 2 * value) / (period + 1)`. When an EMA smooths a series that has
undefined values (EMA of EMA, a signal line), it seeds from the mean of the first `period`
defined values, and an undefined value yields undefined output without advancing the recurrence.
True range is the shipped ATR true range (`high - low` on the first bar). Operations are listed
in evaluation order. `Decimal.ln`, `Decimal.log10`, and `Decimal.sqrt` are correctly rounded in the
engine context. These kinds are not TA-library identities, although development cross-checked
each against TA-Lib where TA-Lib implements the same formula.

#### DEMA, TEMA, and Hull MA

`dema = 2 * E1 - E2` and `tema = (3 * E1 - 3 * E2) + E3`, where `E1 = EMA(source, period)`,
`E2 = EMA(E1, period)`, and `E3 = EMA(E2, period)`. `hma = WMA(2 * WMA(source, period // 2) -
WMA(source, period), isqrt(period))` with the shipped WMA fold (a WMA of period 1 is the value).
First values: `2 * period - 1`, `3 * period - 2`, and `period + isqrt(period) - 1` bars.

#### KAMA

The source on bar `period - 1` seeds KAMA and is not reported. From bar `period` on:
`er = abs(x - x[period]) / sum(abs(x[i] - x[i - 1]))` over the last `period` changes; a zero sum
(flat window) is `er = 1`, as in TA-Lib. `fast = 2 / (fast_period + 1)`,
`slow = 2 / (slow_period + 1)`, `sc = (er * (fast - slow) + slow) ** 2`, and
`kama = previous + sc * (x - previous)`. First value after `period + 1` bars.

#### VWMA

`sum(close * volume) / sum(volume)` over the window. Zero window volume is undefined.

#### Supertrend

Series `value` and `direction`. `ATR` is the shipped Wilder ATR(`period`); `mid = (high + low) /
2`; basic bands are `mid ± multiplier * ATR`. On the first ATR bar the final bands are the basic
bands and the trend is down (`direction = -1`). Afterwards the final upper band becomes the basic
upper band only when that is lower than the previous final upper band or the previous close was
above it; the final lower band symmetrically. A down trend flips up when `close > final upper`; an
up trend flips down when `close < final lower`. `value` is the final lower band in an up trend and
the final upper band in a down trend. This is TradingView's `ta.supertrend` with the direction
sign flipped (1 = up). First values after `period` bars.

#### Parabolic SAR

A port of TA-Lib `SAR` in Decimal. Bar 1 decides the start: short only when `low[0] - low[1]` is
positive and greater than `high[1] - high[0]`; otherwise long. A long starts with SAR `low[0]` and
extreme point `high[1]` (short: `high[0]` and `low[1]`), acceleration `step`. On each bar from 1:
a long whose `low <= SAR` reverses — it reports `max(extreme, prior high, high)`, resets
acceleration to `step`, and moves the extreme to the bar's low; otherwise it reports the SAR, and a
new high raises the extreme and acceleration (`min(acceleration + step, max_step)`). The next SAR
is `SAR + acceleration * (extreme - SAR)`, clamped to at most the prior and current lows (long) or
at least the prior and current highs (short). On bar 1 the "prior" bar is bar 1 itself. Shorts are
symmetric. The first SAR is reported on bar 1 (warmup 2). A bar that touches the SAR reverses, so a
perfectly flat series alternates while the SAR stays at the price.

#### Aroon

Over the last `period + 1` bars: `up = 100 * (period - bars since the highest high) / period`,
`down` likewise from the lowest low, `oscillator = up - down`. Ties pick the most recent bar, so a
flat window reads `100`, `100`, `0`. First values after `period + 1` bars.

#### Ichimoku

`tenkan`, `kijun`, and `senkou_b` are `(highest high + lowest low) / 2` over their windows;
`senkou_a = (tenkan + kijun) / 2`. Values are reported on the bar whose data produced them: no
forward displacement, no chikou (a displaced value at the current bar would need future data;
chikou at bar `t` is `close`). The chart's current cloud is the same declaration with
`offset: kijun_period`. First values after each window; warmup is the longest window.

#### Vortex

For each of the last `period` bars that have a previous bar: `VM+ = abs(high - prior low)`,
`VM- = abs(low - prior high)`, and the shipped true range. `plus = sum(VM+) / sum(TR)` and
`minus = sum(VM-) / sum(TR)`. A zero true-range sum is undefined. First values after
`period + 1` bars.

#### Linear regression

Least squares over the window with `x = 0` (oldest) to `period - 1` (current):
`slope = (n * Sxy - Sx * Sy) / (n * Sxx - Sx ** 2)`, `intercept = (Sy - slope * Sx) / n`, and
`value = intercept + slope * (n - 1)` (the line's value on the current bar). `Sx`, `Sxx`, and the
divisor are exact integers; `Sy` and `Sxy` are oldest-first folds. Matches TA-Lib `LINEARREG` and
`LINEARREG_SLOPE`. First values after `period` bars.

#### TRIX

`100 * (E3 - E3[1]) / E3[1]` for `E3 = EMA(EMA(EMA(close)))`. A zero previous `E3` is undefined.
First value after `3 * period - 1` bars.

#### Stochastic RSI

`raw = 100 * (RSI - lowest RSI) / (highest RSI - lowest RSI)` over the last `stoch_period`
shipped RSI(`rsi_period`) values; an RSI range at or below `1e-30` counts as zero and is
undefined (on flat prices both Wilder averages decay together, so RSI is constant up to 64-digit
rounding, and dividing by that noise would produce arbitrary readings). `k` is the SMA of `raw` over
`k_period` (1 means none) and `d` the SMA of `k` over `d_period`; any undefined value in an SMA
window is undefined. Warmup `rsi_period + stoch_period + k_period + d_period - 2`.

#### PPO

`ppo = 100 * (EMA fast - EMA slow) / EMA slow` (a zero slow EMA is undefined), `signal` is the
EMA of `ppo` over `signal_period`, and `histogram = ppo - signal`. Warmup matches MACD.

#### Ultimate Oscillator

For bars with a previous close: `BP = close - min(low, prior close)` and
`TR = max(high, prior close) - min(low, prior close)`. Each window average is `sum(BP) / sum(TR)`
over the short, medium, and long windows, and `UO = 100 * (4 * short + 2 * medium + long) / 7`.
A zero true-range sum in any window is undefined (TA-Lib instead drops that term). First value
after `long_period + 1` bars.

#### Awesome Oscillator

`SMA(median, fast_period) - SMA(median, slow_period)` with `median = (high + low) / 2`.

#### CMO

`100 * (gains - losses) / (gains + losses)` over the last `period` closing changes, with gains and
losses as plain sums (Chande's definition, not TA-Lib's Wilder smoothing). No change in the
window is undefined. First value after `period + 1` bars.

#### TSI

`tsi = 100 * EMA(EMA(change, long_period), short_period) / EMA(EMA(abs(change), long_period),
short_period)` where `change = close - prior close` (undefined on the first bar). A zero
denominator is undefined. `signal` is the EMA of `tsi` over `signal_period`. Warmup
`long_period + short_period + signal_period - 1`.

#### Keltner and Donchian channels

Keltner: `middle = EMA(close, period)`, `upper/lower = middle ± multiplier * ATR(atr_period)`.
Donchian: `upper` is the highest high and `lower` the lowest low over the window (current bar
included), `middle = (upper + lower) / 2`. Use `offset: 1` for the previous bar's channel.

#### Bollinger %B and bandwidth

On the shipped Bollinger bands (SMA middle, population stdev): `%B = (close - lower) / (upper -
lower)` (equal bands are undefined) and `bandwidth = (upper - lower) / middle` as a fraction.

#### NATR and Choppiness

`natr = 100 * ATR(period) / close`. `choppiness = 100 * log10(sum(TR) / (highest high - lowest
low)) / log10(period)` over the window; a zero high-low range is undefined.

#### Historical volatility

`100 *` the sample (N − 1) stdev of the last `period` values of `ln(close / prior close)` (the
ratio is rounded to the engine context first), using the shipped sample-stdev fold. With
`annualization_periods` the result is multiplied by its square root; without it the value is per
bar. A non-positive close makes its returns undefined. First value after `period + 1` bars.

#### OBV and accumulation/distribution

OBV is `0` on the first bar and then adds the bar's volume on a higher close, subtracts it on a
lower close, and keeps it on an equal close. The A/D line is the running sum of
`((close - low) - (high - close)) / (high - low) * volume`, with `0` for a zero-range bar. Both are
cumulative from the first supplied bar, so the level depends on where the series starts; `signal`
(SMA over `signal_period`) gives start-independent crossovers.

#### CMF, rolling VWAP, and force index

CMF: `sum(multiplier * volume) / sum(volume)` over the window with the A/D multiplier. Rolling
VWAP: `sum(typical * volume) / sum(volume)` with `typical = (high + low + close) / 3`; crypto has
no session, so it is a window of `period` bars. Zero window volume is undefined for both. Force
index: the EMA over `period` of `(close - prior close) * volume`, first defined after
`period + 1` bars.

#### Z-score and percent rank

`zscore = (source - SMA) / population stdev` over the window (a zero stdev is undefined), so a
z-score of ±m sits on Bollinger bands with multiplier m. `percent_rank = 100 * count / period`,
where `count` is how many of the previous `period` values (current bar excluded) are at or below
the current value. First values after `period` and `period + 1` bars.

### EMA

EMA consumes one author-selected OHLCV field. The first value is the arithmetic mean of the first
`period` observations. Later values use:

`ema = ((period - 1) * previous_ema + 2 * value) / (period + 1)`

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

- schema version and `engine` (`thytrader-backtest`);
- exact run, strategy, and dataset fingerprints;
- the identity-bearing exact output-key sequence copied from the strategy snapshot (LTF then HTF
  when a filter is present): `{id}` for single-output kinds, `{id}.{series}` for multi-series kinds;
- one unique, strictly increasing record per evaluation candle;
- every declared output key's canonical value or explicit `null` exactly once in that sequence; and
- the final entry-condition outcome: `matched`, `not_matched`, or `undefined`.

Canonical indicator values are plain decimal text, optionally signed, with no exponent notation and
no trailing zeros ([ADR 0027](../decisions/0027-phase-9-roc-williams-cci.md) extended the unsigned
pattern so ROC and Williams %R can be traced).

Canonical trace JSON uses sorted keys, compact separators, UTF-8, and UTC `Z` timestamps. The complete
trace is addressed by SHA-256. Trace fields are frozen, unknown-field rejecting, and strict about
identity-bearing native strings. A trace contains at least one record; every record contains a
non-empty indicator vector matching the trace's unique declared output-key sequence exactly, with
no missing, extra, duplicate, or reordered entries. Indicator ids cannot contain `.`, so
`{id}.{series}` cannot collide with another indicator id. Traces are not yet persisted; rerunning the exact
publication recreates the same canonical bytes. Canonical serialization revalidates the complete
typed trace before emitting bytes so unchecked model copies cannot acquire fingerprints.

## Read-only operator command

An operator can trace the run behind any backtest result with:

```bash
uv run thytrader-research-evaluate <result_fingerprint> [--outcome matched] [--limit 200] [--cursor C] --pretty
```

Since [ADR 0090](../decisions/0090-research-correctness-optional-take-profit-diagnostics.md) the
command is an HTTP client of `GET /api/v1/backtests/{result_fingerprint}/signal-trace` (a
`run_fingerprint` is resolved to its newest result). The API loads the result, its verified run
and strategy snapshot, re-evaluates the primary product's trace on its read-only dataset volume
off the event loop, and fails closed with HTTP 503 `signal_trace_unavailable` unless
`signal_trace_fingerprint(trace)` equals the result's. The response is one bounded page
(`limit` ≤ 1000) of `SignalTraceRecord`s filtered by `outcome`, plus outcome `counts`,
`total_records`, and `next_cursor`. Earlier versions read PostgreSQL and the Parquet root
directly from the operator's shell, which Compose installs cannot reach, and reported every
failure as one generic message. It still cannot publish or mutate runs, strategies, datasets,
results, or trading state, and it reports the API's redacted reason without database URLs,
artifact content, or credentials.
