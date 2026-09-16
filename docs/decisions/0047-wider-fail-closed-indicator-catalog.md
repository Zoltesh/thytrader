# 0047: Wider fail-closed indicator catalog

- Status: Accepted
- Date: 2026-09-16
- Relates to: [0005](0005-canonical-strategy-schema.md), [0008](0008-deterministic-signal-evaluation.md),
  [0026](0026-phase-9-single-output-indicator-catalog.md),
  [0027](0027-phase-9-roc-williams-cci.md), [0028](0028-phase-9-identity-constant.md),
  [0029](0029-phase-9-wma-momentum-mfi.md), [0032](0032-phase-9-macd-bollinger.md),
  [0042](0042-per-indicator-timeframes.md)

## Context

Phase 9 shipped five fail-closed catalog slices through MACD and Bollinger with referenceable series
ids ([ADR 0032](0032-phase-9-macd-bollinger.md)). Vision and the destination table still asked for a
wider catalog: stochastic, ADX, configurable rolling inputs, and sample standard deviation. Those
stayed out of Phase 9 because ADX's Wilder seed and stochastic's extra series needed their own
contract, and sample stdev was rejected as a hidden `ddof` on population `stdev`.

Per-indicator timeframes are already shipped ([ADR 0042](0042-per-indicator-timeframes.md)). This
slice must keep complete-only candles, no interpolation, no TA-library passthrough, and the same
published strategy semantics in backtest, paper, and live.

## Decision

Keep `schema_version: "1.0"`. Add kinds and relax locked single-source inputs on the same LTF/HTF
registry. Existing kinds and fingerprints stay valid. Engine identifiers do not change.

### Stochastic and ADX

| Kind | Input (locked) | Parameters | Outputs | First defined values |
|------|----------------|------------|---------|----------------------|
| `stochastic` | `high, low, close` | `k_period` (2–100), `d_period` (2–500) | `k`, `d` | `%K` after `k_period` bars; `%D` after `k_period + d_period - 1` bars |
| `adx` | `high, low, close` | `period` (2–100) | `adx`, `plus_di`, `minus_di` | `plus_di` / `minus_di` after `period` bars; `adx` after `2 * period - 1` bars |

Semantics:

- Unknown kinds, unknown fields, unlocked inputs, missing series, and `series` on single-output
  kinds fail closed. Conditions stay tri-state. Insufficient warmup is `null`, not `0`.
- Arithmetic uses `decimal64-half-even-v1`. Windows include the current completed bar and never a
  future bar. No interpolation.
- `stochastic` `%K` is `100 * (close - lowest_low) / (highest_high - lowest_low)` over the inclusive
  `k_period` window, reusing the shipped rolling max/min left-folds. A zero window range yields
  undefined `%K`. `%D` is the shipped SMA of the last `d_period` `%K` values; any undefined `%K` in
  that window yields undefined `%D`. This is fast stochastic: `%D` smooths raw `%K`. There is no
  extra slowing period.
- `adx` true range matches shipped ATR (first bar `high - low`; later `max` of that span and the
  two previous-close displacements). First-bar `+DM` and `-DM` are `0` because there is no previous
  high/low; they participate in the Wilder seed the same way the first ATR true-range does. Later
  `+DM` is the up-move when it is strictly greater than the down-move and positive; `-DM` is the
  down-move when it is strictly greater than the up-move and positive; ties are both `0`. Smoothed
  TR / `+DM` / `-DM` use the shipped ATR Wilder seed and recurrence. `plus_di` is
  `100 * smoothed_+DM / smoothed_TR`; `minus_di` is `100 * smoothed_-DM / smoothed_TR`. A zero
  smoothed TR yields undefined DI. `DX` is `100 * abs(plus_di - minus_di) / (plus_di + minus_di)`.
  A zero DI sum yields undefined `DX` (not `0`). `ADX` is that same Wilder smooth of defined `DX`
  values. Strategy `warmup_bars` for `adx` is `2 * period - 1`, which is necessary when every DX
  after DI warmup is defined; a hole delays the first ADX and stays tri-state.
- Operand `series` is required on these kinds (`k`/`d` or `adx`/`plus_di`/`minus_di`). Trace keys
  are `{id}.{series}`.
- No TA-lib, TradingView, or provider formula passthrough.

### Configurable rolling inputs

These single-output kinds accept one author-selected OHLCV field (`open`, `high`, `low`, `close`,
or `volume`): `ema`, `sma`, `wma`, `highest`, `lowest`, `stdev`, `stdev_sample`, `roc`, `momentum`.
Canonical JSON still requires `input`. Existing close/high/low/volume documents remain valid.

Still locked:

- `rsi` → `close`
- `volume_sma` → `volume`
- `macd` / `bollinger` → `close`
- `atr` / `williams_r` / `cci` / `stochastic` / `adx` → canonical `["high", "low", "close"]`
- `mfi` → `["high", "low", "close", "volume"]`
- `identity` still selects one field; `constant` still omits `input`

HLC tuples on a configurable kind fail closed. `required_fields` must still include every consumed
field, including SMA-of-volume.

### Sample stdev

| Kind | Input | Parameters | Output | First defined value |
|------|-------|------------|--------|---------------------|
| `stdev_sample` | one of `open`, `high`, `low`, `close`, `volume` | `period` (2–500) | sample stdev of the inclusive window | after `period` bars |

`stdev` remains population (`divide by period`). `stdev_sample` uses the same left-fold mean and
squared-deviation sum, then divides by `period - 1` (`period >= 2`). Non-positive variance yields
`0`. Extra `ddof` on either kind fails closed. Bollinger bands stay on population `stdev`.

### Shared runtime rules

Research V1/V2/V3, paper, and live consume these kinds on the same published document. Optional
per-indicator `timeframe` ([ADR 0042](0042-per-indicator-timeframes.md)) and `htf_filter` apply
unchanged: last-completed complete-only bars, no interpolation. Adding kinds does not reinterpret
existing published strategies. Changing a shipped formula would still require a new engine contract
([ADR 0008](0008-deterministic-signal-evaluation.md)).

Operator `indicators` lists only implemented kinds. `parameter_kind` `stochastic` covers
`k_period` / `d_period`. Configurable kinds list all five allowed inputs. Multi-series kinds list
`outputs`.

No ops-contract bump and no Alembic revision: agents discover the catalog through
`thytrader-operator indicators`, as with prior catalog slices.

## Consequences

- Authors can write stochastic `%K`/`%D` crossovers, ADX / DI filters, SMA-of-high, highest-close
  channels, and sample-stdev volatility gates without a TA library.
- Support matrices and skills must list the new kinds and stop telling agents that stochastic and
  ADX are unlisted.
- Further catalog kinds still fail closed until their own ADR. TA passthrough and interpolated
  candles stay deferred.

## Alternatives considered

- **TA-library passthrough:** rejected; every kind needs an explicit warmup, left-fold, and
  undefined-data contract.
- **`ddof` on `stdev`:** rejected in [ADR 0026](0026-phase-9-single-output-indicator-catalog.md);
  sample vs population is a second kind, not a hidden parameter.
- **Slow stochastic extra smoothing period:** rejected; `%D` as SMA of raw `%K` is one contract.
  A later kind can add slowing without reinterpreting this `%K`/`%D`.
- **Configurable input on RSI, MACD, Bollinger, ATR, Williams, CCI, MFI, stochastic, ADX:**
  rejected; those formulas consume specific fields.
- **New engine-contract version:** rejected; additive catalog, no change to shipped formulas.
- **Interpolating missing candles or in-progress extra-TF bars:** rejected; complete-only and
  last-completed rules already on main stay in force.
