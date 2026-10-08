"""Catalog entries for the later volatility, volume, and statistical indicator kinds.

Each tuple keeps catalog order; ``INDICATOR_KIND_SPECS`` concatenates them in that
category order after the trend and momentum entries.
"""

from __future__ import annotations

from thytrader.strategies.indicator_spec_model import (
    _BOLLINGER_PARAMETERS,
    _CLOSE,
    _STATISTICAL,
    _VOLATILITY,
    _VOLUME,
    IndicatorKindSpec,
    IndicatorParameterSpec,
    _configurable,
    _integer,
    _locked,
    _multiplier,
    _period,
)
from thytrader.strategies.models import (
    CLOSE_VOLUME_INPUT,
    HL_INPUT,
    HLC_INPUT,
    HLCV_INPUT,
    IndicatorKind,
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
