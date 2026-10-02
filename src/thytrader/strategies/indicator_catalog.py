"""Descriptive registry of every implemented indicator kind.

The strategy schema (:mod:`thytrader.strategies.models`) is the authority on what a
document may declare; this registry describes those kinds for people and agents:
category, label, one-line help, input policy, parameter bounds with builder
defaults, output series, and the warmup formula. The operator ``indicators`` report
and the browser builder catalog (``web/src/lib/indicator-catalog.json``) are both
rendered from it, and ``tests/strategies/test_indicator_catalog.py`` proves every
bound, default, output, and warmup here agrees with the schema validators.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from thytrader.strategies.models import (
    CLOSE_VOLUME_INPUT,
    HL_INPUT,
    HLC_INPUT,
    HLCV_INPUT,
    IndicatorDefinition,
    IndicatorKind,
    indicator_min_warmup,
    indicator_output_series,
)

InputMode = Literal["configurable", "locked", "none"]
ParameterValueType = Literal["integer", "decimal"]
ParameterKind = Literal[
    "period",
    "none",
    "value",
    "macd",
    "bollinger",
    "stochastic",
    "kama",
    "supertrend",
    "parabolic_sar",
    "ichimoku",
    "stochastic_rsi",
    "ultimate_oscillator",
    "awesome_oscillator",
    "tsi",
    "keltner",
    "historical_volatility",
    "signal",
]
OHLCV_FIELDS: tuple[str, ...] = ("open", "high", "low", "close", "volume")


class IndicatorCategory(StrEnum):
    """Builder and report grouping for indicator kinds."""

    TREND = "trend"
    MOMENTUM = "momentum"
    VOLATILITY = "volatility"
    VOLUME = "volume"
    STATISTICAL = "statistical"
    PRICE = "price"


@dataclass(frozen=True, slots=True)
class IndicatorParameterSpec:
    """One declared parameter: bounds (inclusive unless noted), builder default, and help.

    Integer bounds and defaults are ``int``; decimal ones are canonical decimal text.
    ``None`` bounds mean unbounded. ``optional`` parameters are omitted from the
    document unless the author sets them; their ``default`` is ``None``.
    """

    name: str
    label: str
    value_type: ParameterValueType
    minimum: int | str | None
    maximum: int | str | None
    default: int | str | None
    help: str
    exclusive_minimum: bool = False
    optional: bool = False


@dataclass(frozen=True, slots=True)
class IndicatorKindSpec:
    """Everything a person or agent needs to declare one indicator kind correctly."""

    kind: IndicatorKind
    label: str
    category: IndicatorCategory
    summary: str
    input_mode: InputMode
    inputs: tuple[str, ...]
    default_input: str | tuple[str, ...] | None
    parameter_kind: ParameterKind
    parameters: tuple[IndicatorParameterSpec, ...]
    warmup: str
    constraints: tuple[str, ...] = ()

    @property
    def outputs(self) -> tuple[str, ...]:
        """Return declared series names, or an empty tuple for single-output kinds."""
        return indicator_output_series(self.kind) or ()


def _period(
    default: int,
    *,
    maximum: int = 500,
    label: str = "Period",
    help_text: str = "Bars in the rolling window (current bar included).",
) -> IndicatorParameterSpec:
    """Return the shared ``period`` parameter spec."""
    return IndicatorParameterSpec(
        name="period",
        label=label,
        value_type="integer",
        minimum=2,
        maximum=maximum,
        default=default,
        help=help_text,
    )


def _integer(
    name: str,
    label: str,
    *,
    default: int,
    help_text: str,
    minimum: int = 2,
    maximum: int = 500,
) -> IndicatorParameterSpec:
    """Return one bounded integer parameter spec."""
    return IndicatorParameterSpec(
        name=name,
        label=label,
        value_type="integer",
        minimum=minimum,
        maximum=maximum,
        default=default,
        help=help_text,
    )


def _multiplier(name: str, label: str, default: str, help_text: str) -> IndicatorParameterSpec:
    """Return a band multiplier spec: decimal greater than 0 and at most 10."""
    return IndicatorParameterSpec(
        name=name,
        label=label,
        value_type="decimal",
        minimum="0",
        maximum="10",
        default=default,
        help=help_text,
        exclusive_minimum=True,
    )


def _configurable(
    kind: IndicatorKind,
    label: str,
    category: IndicatorCategory,
    summary: str,
    *,
    parameters: tuple[IndicatorParameterSpec, ...],
    warmup: str,
    default_input: str = "close",
    parameter_kind: ParameterKind = "period",
    constraints: tuple[str, ...] = (),
) -> IndicatorKindSpec:
    """Return a spec for a kind that reads one author-selected OHLCV field."""
    return IndicatorKindSpec(
        kind=kind,
        label=label,
        category=category,
        summary=summary,
        input_mode="configurable",
        inputs=OHLCV_FIELDS,
        default_input=default_input,
        parameter_kind=parameter_kind,
        parameters=parameters,
        warmup=warmup,
        constraints=constraints,
    )


def _locked(
    kind: IndicatorKind,
    label: str,
    category: IndicatorCategory,
    summary: str,
    *,
    inputs: tuple[str, ...],
    parameters: tuple[IndicatorParameterSpec, ...],
    warmup: str,
    parameter_kind: ParameterKind = "period",
    constraints: tuple[str, ...] = (),
) -> IndicatorKindSpec:
    """Return a spec for a kind whose input is one fixed field or canonical field tuple."""
    default_input: str | tuple[str, ...] = inputs[0] if len(inputs) == 1 else inputs
    return IndicatorKindSpec(
        kind=kind,
        label=label,
        category=category,
        summary=summary,
        input_mode="locked",
        inputs=inputs,
        default_input=default_input,
        parameter_kind=parameter_kind,
        parameters=parameters,
        warmup=warmup,
        constraints=constraints,
    )


_TREND = IndicatorCategory.TREND
_MOMENTUM = IndicatorCategory.MOMENTUM
_VOLATILITY = IndicatorCategory.VOLATILITY
_VOLUME = IndicatorCategory.VOLUME
_STATISTICAL = IndicatorCategory.STATISTICAL
_PRICE = IndicatorCategory.PRICE
_CLOSE = ("close",)
_FAST_SLOW_SIGNAL = (
    _integer("fast_period", "Fast period", default=12, maximum=499, help_text="Fast EMA window."),
    _integer("slow_period", "Slow period", default=26, minimum=3, help_text="Slow EMA window."),
    _integer("signal_period", "Signal period", default=9, help_text="EMA of the line."),
)
_BOLLINGER_PARAMETERS = (
    _period(20, help_text="SMA and population-stdev window (current bar included)."),
    _multiplier(
        "stdev_multiplier",
        "Stdev multiplier",
        "2",
        "Band distance in population standard deviations.",
    ),
)

_EXISTING_SPECS: tuple[IndicatorKindSpec, ...] = (
    _configurable(
        IndicatorKind.EMA,
        "EMA",
        _TREND,
        "Exponential moving average, seeded by the SMA of the first period values.",
        parameters=(_period(20, help_text="EMA window; smoothing is 2 / (period + 1)."),),
        warmup="period",
    ),
    _configurable(
        IndicatorKind.SMA,
        "SMA",
        _TREND,
        "Arithmetic mean of the last period values.",
        parameters=(_period(20),),
        warmup="period",
    ),
    _locked(
        IndicatorKind.RSI,
        "RSI",
        _MOMENTUM,
        "Wilder relative strength index (0 to 100) over period closing changes.",
        inputs=_CLOSE,
        parameters=(_period(14, maximum=100, help_text="Closing changes in the Wilder average."),),
        warmup="period + 1",
    ),
    _locked(
        IndicatorKind.ATR,
        "ATR",
        _VOLATILITY,
        "Wilder average true range. Initial and trailing stops reference an ATR.",
        inputs=HLC_INPUT,
        parameters=(_period(14, maximum=100, help_text="True ranges in the Wilder average."),),
        warmup="period",
    ),
    _locked(
        IndicatorKind.VOLUME_SMA,
        "Volume SMA",
        _VOLUME,
        "Arithmetic mean of volume over period bars.",
        inputs=("volume",),
        parameters=(_period(20),),
        warmup="period",
    ),
    _configurable(
        IndicatorKind.HIGHEST,
        "Highest",
        _PRICE,
        "Highest value of the source over period bars, current bar included.",
        parameters=(_period(20),),
        warmup="period",
        default_input="high",
    ),
    _configurable(
        IndicatorKind.LOWEST,
        "Lowest",
        _PRICE,
        "Lowest value of the source over period bars, current bar included.",
        parameters=(_period(20),),
        warmup="period",
        default_input="low",
    ),
    _configurable(
        IndicatorKind.STDEV,
        "Stdev",
        _VOLATILITY,
        "Population standard deviation of the source over period bars.",
        parameters=(_period(20),),
        warmup="period",
    ),
    _configurable(
        IndicatorKind.STDEV_SAMPLE,
        "Sample stdev",
        _VOLATILITY,
        "Sample (N - 1) standard deviation of the source over period bars.",
        parameters=(_period(20),),
        warmup="period",
    ),
    _configurable(
        IndicatorKind.ROC,
        "ROC",
        _MOMENTUM,
        "Percent change versus the value exactly period bars ago.",
        parameters=(_period(10, help_text="Lookback in bars."),),
        warmup="period + 1",
    ),
    _locked(
        IndicatorKind.WILLIAMS_R,
        "Williams %R",
        _MOMENTUM,
        "Close within the period high-low range: -100 at the low, 0 at the high.",
        inputs=HLC_INPUT,
        parameters=(_period(14, maximum=100),),
        warmup="period",
    ),
    _locked(
        IndicatorKind.CCI,
        "CCI",
        _MOMENTUM,
        "Commodity channel index of typical price (Lambert constant 0.015).",
        inputs=HLC_INPUT,
        parameters=(_period(20, maximum=100),),
        warmup="period",
    ),
    _configurable(
        IndicatorKind.WMA,
        "WMA",
        _TREND,
        "Linearly weighted moving average; the newest bar weighs period.",
        parameters=(_period(20),),
        warmup="period",
    ),
    _configurable(
        IndicatorKind.MOMENTUM,
        "Momentum",
        _MOMENTUM,
        "Source minus the source exactly period bars ago.",
        parameters=(_period(10, help_text="Lookback in bars."),),
        warmup="period + 1",
    ),
    _locked(
        IndicatorKind.MFI,
        "MFI",
        _VOLUME,
        "Money flow index (0 to 100): volume-weighted RSI of typical price.",
        inputs=HLCV_INPUT,
        parameters=(_period(14, maximum=100, help_text="Typical-price changes in the window."),),
        warmup="period + 1",
    ),
    _locked(
        IndicatorKind.MACD,
        "MACD",
        _MOMENTUM,
        "EMA(fast) - EMA(slow) with an EMA signal line and histogram.",
        inputs=_CLOSE,
        parameters=_FAST_SLOW_SIGNAL,
        warmup="slow_period + signal_period - 1",
        parameter_kind="macd",
        constraints=("fast_period < slow_period",),
    ),
    _locked(
        IndicatorKind.BOLLINGER,
        "Bollinger",
        _VOLATILITY,
        "SMA middle band with bands a multiple of population stdev away.",
        inputs=_CLOSE,
        parameters=_BOLLINGER_PARAMETERS,
        warmup="period",
        parameter_kind="bollinger",
    ),
    _locked(
        IndicatorKind.STOCHASTIC,
        "Stochastic",
        _MOMENTUM,
        "Fast stochastic: %K is close within the high-low range, %D its SMA.",
        inputs=HLC_INPUT,
        parameters=(
            _integer(
                "k_period", "%K period", default=14, maximum=100, help_text="High-low window."
            ),
            _integer("d_period", "%D period", default=3, help_text="SMA of %K."),
        ),
        warmup="k_period + d_period - 1",
        parameter_kind="stochastic",
    ),
    _locked(
        IndicatorKind.ADX,
        "ADX",
        _TREND,
        "Wilder ADX trend strength with +DI and -DI.",
        inputs=HLC_INPUT,
        parameters=(_period(14, maximum=100, help_text="Wilder smoothing period."),),
        warmup="2 * period - 1",
    ),
    IndicatorKindSpec(
        kind=IndicatorKind.IDENTITY,
        label="OHLCV",
        category=_PRICE,
        summary="Copies one candle field (open, high, low, close, or volume).",
        input_mode="configurable",
        inputs=OHLCV_FIELDS,
        default_input="close",
        parameter_kind="none",
        parameters=(),
        warmup="1",
    ),
    IndicatorKindSpec(
        kind=IndicatorKind.CONSTANT,
        label="Constant",
        category=_PRICE,
        summary="A fixed level on every bar, for example an RSI threshold to cross.",
        input_mode="none",
        inputs=(),
        default_input=None,
        parameter_kind="value",
        parameters=(
            IndicatorParameterSpec(
                name="value",
                label="Value",
                value_type="decimal",
                minimum=None,
                maximum=None,
                default="50",
                help="Exact decimal level, for example 30 or -0.5.",
            ),
        ),
        warmup="1",
    ),
)

_TREND_SPECS: tuple[IndicatorKindSpec, ...] = (
    _configurable(
        IndicatorKind.DEMA,
        "DEMA",
        _TREND,
        "Double EMA, 2 * EMA - EMA(EMA): less lag than a plain EMA.",
        parameters=(_period(20, help_text="Window of both chained EMAs."),),
        warmup="2 * period - 1",
    ),
    _configurable(
        IndicatorKind.TEMA,
        "TEMA",
        _TREND,
        "Triple EMA, 3 * EMA - 3 * EMA(EMA) + EMA(EMA(EMA)).",
        parameters=(_period(20, help_text="Window of all three chained EMAs."),),
        warmup="3 * period - 2",
    ),
    _configurable(
        IndicatorKind.HMA,
        "Hull MA",
        _TREND,
        "Hull moving average: WMA(2 * WMA(n/2) - WMA(n), √n), with n/2 and √n floored.",
        parameters=(_period(20, help_text="Hull length n."),),
        warmup="period + floor(sqrt(period)) - 1",
    ),
    _configurable(
        IndicatorKind.KAMA,
        "KAMA",
        _TREND,
        "Kaufman adaptive MA: moves fast in efficient trends, slowly in noise.",
        parameters=(
            _period(10, maximum=100, help_text="Efficiency-ratio lookback in price changes."),
            _integer(
                "fast_period",
                "Fast period",
                default=2,
                maximum=100,
                help_text="EMA-equivalent period of the fastest smoothing.",
            ),
            _integer(
                "slow_period",
                "Slow period",
                default=30,
                minimum=3,
                help_text="EMA-equivalent period of the slowest smoothing.",
            ),
        ),
        warmup="period + 1",
        parameter_kind="kama",
        constraints=("fast_period < slow_period",),
    ),
    _locked(
        IndicatorKind.VWMA,
        "VWMA",
        _TREND,
        "Volume-weighted moving average of close.",
        inputs=CLOSE_VOLUME_INPUT,
        parameters=(_period(20),),
        warmup="period",
    ),
    _locked(
        IndicatorKind.SUPERTREND,
        "Supertrend",
        _TREND,
        "ATR band that trails the trend; direction is 1 (up) or -1 (down).",
        inputs=HLC_INPUT,
        parameters=(
            _period(10, maximum=100, label="ATR period", help_text="Wilder ATR period."),
            _multiplier("multiplier", "Multiplier", "3", "ATR multiple for the bands."),
        ),
        warmup="period",
        parameter_kind="supertrend",
    ),
    _locked(
        IndicatorKind.PARABOLIC_SAR,
        "Parabolic SAR",
        _TREND,
        "Wilder stop-and-reverse that accelerates toward the trend's extreme point.",
        inputs=HL_INPUT,
        parameters=(
            IndicatorParameterSpec(
                name="step",
                label="Step",
                value_type="decimal",
                minimum="0",
                maximum="1",
                default="0.02",
                help="Acceleration start and increment per new extreme.",
                exclusive_minimum=True,
            ),
            IndicatorParameterSpec(
                name="max_step",
                label="Max step",
                value_type="decimal",
                minimum="0",
                maximum="1",
                default="0.2",
                help="Acceleration cap (at least step).",
                exclusive_minimum=True,
            ),
        ),
        warmup="2",
        parameter_kind="parabolic_sar",
        constraints=("step <= max_step",),
    ),
    _locked(
        IndicatorKind.AROON,
        "Aroon",
        _TREND,
        "Bars since the period high and low, scaled 0 to 100, and their difference.",
        inputs=HL_INPUT,
        parameters=(_period(25, help_text="Lookback; the window holds period + 1 bars."),),
        warmup="period + 1",
    ),
    _locked(
        IndicatorKind.ICHIMOKU,
        "Ichimoku",
        _TREND,
        "Conversion, base, and leading-span midpoints on the current bar (no displacement).",
        inputs=HL_INPUT,
        parameters=(
            _integer(
                "tenkan_period",
                "Tenkan period",
                default=9,
                maximum=498,
                help_text="Conversion line.",
            ),
            _integer(
                "kijun_period",
                "Kijun period",
                default=26,
                minimum=3,
                maximum=499,
                help_text="Base line.",
            ),
            _integer(
                "senkou_b_period",
                "Senkou B period",
                default=52,
                minimum=4,
                help_text="Leading span B midpoint window.",
            ),
        ),
        warmup="max(tenkan_period, kijun_period, senkou_b_period)",
        parameter_kind="ichimoku",
        constraints=("tenkan_period < kijun_period < senkou_b_period",),
    ),
    _locked(
        IndicatorKind.VORTEX,
        "Vortex",
        _TREND,
        "Upward (VI+) and downward (VI-) vortex movement over true range.",
        inputs=HLC_INPUT,
        parameters=(_period(14, help_text="Previous-bar comparisons in each sum."),),
        warmup="period + 1",
    ),
    _configurable(
        IndicatorKind.LINEAR_REGRESSION,
        "Linear regression",
        _TREND,
        "Least-squares line over period bars: endpoint value and slope per bar.",
        parameters=(_period(20),),
        warmup="period",
    ),
    _locked(
        IndicatorKind.TRIX,
        "TRIX",
        _TREND,
        "Percent change of a triple-smoothed EMA of close.",
        inputs=_CLOSE,
        parameters=(_period(15, help_text="Window of each of the three EMAs."),),
        warmup="3 * period - 1",
    ),
)

_MOMENTUM_SPECS: tuple[IndicatorKindSpec, ...] = (
    _locked(
        IndicatorKind.STOCHASTIC_RSI,
        "Stochastic RSI",
        _MOMENTUM,
        "Stochastic of RSI (0 to 100) with SMA-smoothed %K and %D.",
        inputs=_CLOSE,
        parameters=(
            _integer("rsi_period", "RSI period", default=14, maximum=100, help_text="Wilder RSI."),
            _integer(
                "stoch_period",
                "Stochastic period",
                default=14,
                maximum=100,
                help_text="RSI high-low window.",
            ),
            _integer(
                "k_period",
                "%K smoothing",
                default=3,
                minimum=1,
                maximum=100,
                help_text="SMA of the raw stochastic RSI (1 = none).",
            ),
            _integer(
                "d_period",
                "%D smoothing",
                default=3,
                minimum=1,
                maximum=100,
                help_text="SMA of %K.",
            ),
        ),
        warmup="rsi_period + stoch_period + k_period + d_period - 2",
        parameter_kind="stochastic_rsi",
    ),
    _locked(
        IndicatorKind.PPO,
        "PPO",
        _MOMENTUM,
        "Percentage price oscillator: 100 * (EMA fast - EMA slow) / EMA slow, with signal.",
        inputs=_CLOSE,
        parameters=_FAST_SLOW_SIGNAL,
        warmup="slow_period + signal_period - 1",
        parameter_kind="macd",
        constraints=("fast_period < slow_period",),
    ),
    _locked(
        IndicatorKind.ULTIMATE_OSCILLATOR,
        "Ultimate Oscillator",
        _MOMENTUM,
        "Buying pressure over true range in three windows, weighted 4:2:1 (0 to 100).",
        inputs=HLC_INPUT,
        parameters=(
            _integer(
                "short_period",
                "Short period",
                default=7,
                maximum=98,
                help_text="Shortest window, weight 4.",
            ),
            _integer(
                "medium_period",
                "Medium period",
                default=14,
                minimum=3,
                maximum=99,
                help_text="Middle window, weight 2.",
            ),
            _integer(
                "long_period",
                "Long period",
                default=28,
                minimum=4,
                maximum=100,
                help_text="Longest window, weight 1.",
            ),
        ),
        warmup="long_period + 1",
        parameter_kind="ultimate_oscillator",
        constraints=("short_period < medium_period < long_period",),
    ),
    _locked(
        IndicatorKind.AWESOME_OSCILLATOR,
        "Awesome Oscillator",
        _MOMENTUM,
        "SMA(fast) - SMA(slow) of the median price (high + low) / 2.",
        inputs=HL_INPUT,
        parameters=(
            _integer(
                "fast_period",
                "Fast period",
                default=5,
                maximum=499,
                help_text="Fast median SMA.",
            ),
            _integer(
                "slow_period",
                "Slow period",
                default=34,
                minimum=3,
                help_text="Slow median SMA.",
            ),
        ),
        warmup="slow_period",
        parameter_kind="awesome_oscillator",
        constraints=("fast_period < slow_period",),
    ),
    _locked(
        IndicatorKind.CMO,
        "CMO",
        _MOMENTUM,
        "Chande momentum oscillator (-100 to 100) from summed gains and losses.",
        inputs=_CLOSE,
        parameters=(_period(9, maximum=100, help_text="Closing changes in the sums."),),
        warmup="period + 1",
    ),
    _locked(
        IndicatorKind.TSI,
        "TSI",
        _MOMENTUM,
        "True strength index: double-smoothed momentum over its absolute value, with signal.",
        inputs=_CLOSE,
        parameters=(
            _integer(
                "long_period",
                "Long period",
                default=25,
                minimum=3,
                help_text="First EMA smoothing.",
            ),
            _integer(
                "short_period",
                "Short period",
                default=13,
                maximum=499,
                help_text="Second EMA smoothing.",
            ),
            _integer("signal_period", "Signal period", default=13, help_text="EMA of TSI."),
        ),
        warmup="long_period + short_period + signal_period - 1",
        parameter_kind="tsi",
        constraints=("short_period < long_period",),
    ),
)

_VOLATILITY_SPECS: tuple[IndicatorKindSpec, ...] = (
    _locked(
        IndicatorKind.KELTNER,
        "Keltner",
        _VOLATILITY,
        "EMA middle line with bands an ATR multiple away.",
        inputs=HLC_INPUT,
        parameters=(
            _period(20, help_text="EMA window of close for the middle line."),
            _integer(
                "atr_period",
                "ATR period",
                default=10,
                maximum=100,
                help_text="Wilder ATR period for the band distance.",
            ),
            _multiplier("multiplier", "Multiplier", "2", "ATR multiple for the bands."),
        ),
        warmup="max(period, atr_period)",
        parameter_kind="keltner",
    ),
    _locked(
        IndicatorKind.DONCHIAN,
        "Donchian",
        _VOLATILITY,
        "Highest high, lowest low, and their midpoint over period bars, current bar included.",
        inputs=HL_INPUT,
        parameters=(_period(20),),
        warmup="period",
    ),
    _locked(
        IndicatorKind.BOLLINGER_PERCENT_B,
        "Bollinger %B",
        _VOLATILITY,
        "Close position within the Bollinger bands: 0 at the lower band, 1 at the upper.",
        inputs=_CLOSE,
        parameters=_BOLLINGER_PARAMETERS,
        warmup="period",
        parameter_kind="bollinger",
    ),
    _locked(
        IndicatorKind.BOLLINGER_BANDWIDTH,
        "Bollinger bandwidth",
        _VOLATILITY,
        "Bollinger band width as a fraction of the middle band: (upper - lower) / middle.",
        inputs=_CLOSE,
        parameters=_BOLLINGER_PARAMETERS,
        warmup="period",
        parameter_kind="bollinger",
    ),
    _locked(
        IndicatorKind.NATR,
        "NATR",
        _VOLATILITY,
        "Normalized ATR: Wilder ATR as a percent of close.",
        inputs=HLC_INPUT,
        parameters=(_period(14, maximum=100, help_text="Wilder ATR period."),),
        warmup="period",
    ),
    _locked(
        IndicatorKind.CHOPPINESS,
        "Choppiness",
        _VOLATILITY,
        "100 * log10(ΣTR / range) / log10(period): near 100 choppy, near 0 trending.",
        inputs=HLC_INPUT,
        parameters=(_period(14),),
        warmup="period",
    ),
    _locked(
        IndicatorKind.HISTORICAL_VOLATILITY,
        "Historical volatility",
        _VOLATILITY,
        "Sample stdev of close log returns, in percent; annualized only when asked.",
        inputs=_CLOSE,
        parameters=(
            _period(20, help_text="Log returns in the sample."),
            IndicatorParameterSpec(
                name="annualization_periods",
                label="Annualization periods",
                value_type="integer",
                minimum=1,
                maximum=525_600,
                default=None,
                help="Bars per year to scale by √ (365 daily, 8760 hourly); empty = per bar.",
                optional=True,
            ),
        ),
        warmup="period + 1",
        parameter_kind="historical_volatility",
    ),
)

_VOLUME_SPECS: tuple[IndicatorKindSpec, ...] = (
    _locked(
        IndicatorKind.OBV,
        "OBV",
        _VOLUME,
        "On-balance volume, cumulative from the first supplied bar, with an SMA signal line.",
        inputs=CLOSE_VOLUME_INPUT,
        parameters=(
            _integer("signal_period", "Signal period", default=20, help_text="SMA of OBV."),
        ),
        warmup="signal_period",
        parameter_kind="signal",
    ),
    _locked(
        IndicatorKind.CMF,
        "Chaikin Money Flow",
        _VOLUME,
        "Money-flow volume over volume for period bars (-1 to 1).",
        inputs=HLCV_INPUT,
        parameters=(_period(20),),
        warmup="period",
    ),
    _locked(
        IndicatorKind.ACCUMULATION_DISTRIBUTION,
        "A/D line",
        _VOLUME,
        "Accumulation/distribution: cumulative money-flow volume with an SMA signal line.",
        inputs=HLCV_INPUT,
        parameters=(
            _integer("signal_period", "Signal period", default=20, help_text="SMA of A/D."),
        ),
        warmup="signal_period",
        parameter_kind="signal",
    ),
    _locked(
        IndicatorKind.VWAP,
        "Rolling VWAP",
        _VOLUME,
        "Volume-weighted typical price over the last period bars (crypto has no session).",
        inputs=HLCV_INPUT,
        parameters=(_period(20),),
        warmup="period",
    ),
    _locked(
        IndicatorKind.FORCE_INDEX,
        "Force index",
        _VOLUME,
        "EMA of (close - previous close) * volume.",
        inputs=CLOSE_VOLUME_INPUT,
        parameters=(_period(13, help_text="EMA window of the raw force."),),
        warmup="period + 1",
    ),
)

_STATISTICAL_SPECS: tuple[IndicatorKindSpec, ...] = (
    _configurable(
        IndicatorKind.ZSCORE,
        "Z-score",
        _STATISTICAL,
        "(source - SMA) / population stdev over period bars.",
        parameters=(_period(20),),
        warmup="period",
    ),
    _configurable(
        IndicatorKind.PERCENT_RANK,
        "Percent rank",
        _STATISTICAL,
        "Percent of the previous period values at or below the current value.",
        parameters=(_period(20, help_text="Previous values compared (current bar excluded)."),),
        warmup="period + 1",
    ),
)

INDICATOR_KIND_SPECS: tuple[IndicatorKindSpec, ...] = (
    *_EXISTING_SPECS,
    *_TREND_SPECS,
    *_MOMENTUM_SPECS,
    *_VOLATILITY_SPECS,
    *_VOLUME_SPECS,
    *_STATISTICAL_SPECS,
)
"""Every implemented kind: the historical 21 first, then later slices by category."""

_SPECS_BY_KIND: dict[IndicatorKind, IndicatorKindSpec] = {
    spec.kind: spec for spec in INDICATOR_KIND_SPECS
}


def indicator_kind_spec(kind: IndicatorKind) -> IndicatorKindSpec:
    """Return the descriptive spec for one implemented kind."""
    return _SPECS_BY_KIND[kind]


def default_parameters(spec: IndicatorKindSpec) -> dict[str, int | str]:
    """Return the builder default parameter object (optional parameters omitted)."""
    return {
        parameter.name: parameter.default
        for parameter in spec.parameters
        if parameter.default is not None
    }


def default_indicator_definition(
    spec: IndicatorKindSpec,
    indicator_id: str = "indicator",
    *,
    offset: int | None = None,
) -> IndicatorDefinition:
    """Validate one declaration of ``spec`` with its default input and parameters."""
    payload: dict[str, object] = {
        "id": indicator_id,
        "kind": spec.kind.value,
        "parameters": default_parameters(spec),
    }
    if spec.default_input is not None:
        payload["input"] = spec.default_input
    if offset is not None:
        payload["offset"] = offset
    return IndicatorDefinition.model_validate(payload)


def default_warmup_bars(spec: IndicatorKindSpec) -> int:
    """Return the schema warmup of one default declaration (no offset)."""
    return indicator_min_warmup(default_indicator_definition(spec))


def integer_parameter_bounds(spec: IndicatorKindSpec) -> tuple[int | None, int | None]:
    """Return the smallest integer minimum and largest integer maximum, or ``None``s.

    This reproduces the historical ``period_min`` / ``period_max`` report fields: the
    bounds of the kind's required integer (period-like) parameters, ignoring decimal ones.
    """
    minimums = [
        parameter.minimum
        for parameter in spec.parameters
        if parameter.value_type == "integer"
        and not parameter.optional
        and isinstance(parameter.minimum, int)
    ]
    maximums = [
        parameter.maximum
        for parameter in spec.parameters
        if parameter.value_type == "integer"
        and not parameter.optional
        and isinstance(parameter.maximum, int)
    ]
    return (min(minimums, default=None), max(maximums, default=None))
