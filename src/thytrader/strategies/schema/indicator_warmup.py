"""Minimum warmup bars each declared indicator needs before its first value."""

from __future__ import annotations

from math import isqrt
from typing import TYPE_CHECKING

from thytrader.strategies.schema.indicator_definition import indicator_offset
from thytrader.strategies.schema.indicator_parameters import (
    AwesomeOscillatorIndicatorParameters,
    BollingerIndicatorParameters,
    HistoricalVolatilityIndicatorParameters,
    IchimokuIndicatorParameters,
    IndicatorKind,
    IndicatorParameterBlock,
    IndicatorParameters,
    KamaIndicatorParameters,
    KeltnerIndicatorParameters,
    MacdIndicatorParameters,
    ParabolicSarIndicatorParameters,
    SignalLineIndicatorParameters,
    StochasticIndicatorParameters,
    StochasticRsiIndicatorParameters,
    SupertrendIndicatorParameters,
    TsiIndicatorParameters,
    UltimateOscillatorIndicatorParameters,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from thytrader.strategies.schema.indicator_definition import IndicatorDefinition
    from thytrader.strategies.schema.primitives import _FrozenModel


_LOOKBACK_WARMUP_KINDS = frozenset(
    {
        IndicatorKind.RSI,
        IndicatorKind.ROC,
        IndicatorKind.MOMENTUM,
        IndicatorKind.MFI,
        IndicatorKind.AROON,
        IndicatorKind.VORTEX,
        IndicatorKind.CMO,
        IndicatorKind.FORCE_INDEX,
        IndicatorKind.PERCENT_RANK,
        IndicatorKind.HISTORICAL_VOLATILITY,
    }
)
_UNIT_WARMUP_KINDS = frozenset({IndicatorKind.IDENTITY, IndicatorKind.CONSTANT})


def indicator_min_warmup(indicator: IndicatorDefinition) -> int:
    """Return the closed-bar count required before one indicator produces a value."""
    return _indicator_min_warmup(indicator)


def _indicator_min_warmup(indicator: IndicatorDefinition) -> int:
    """Return closed bars needed before every output is defined, including the bar lag."""
    return _base_min_warmup(indicator) + indicator_offset(indicator)


def _base_min_warmup(indicator: IndicatorDefinition) -> int:
    """Return closed bars needed before every output of the unlagged series is defined."""
    if indicator.kind in _UNIT_WARMUP_KINDS:
        return 1
    rule = _WARMUP_RULES.get(indicator.kind)
    if rule is not None:
        return rule(indicator.parameters)
    extra = 1 if indicator.kind in _LOOKBACK_WARMUP_KINDS else 0
    return _parameters_as(indicator.parameters, IndicatorParameters).period + extra


def _parameters_as[ParametersT: _FrozenModel](
    parameters: IndicatorParameterBlock,
    model: type[ParametersT],
) -> ParametersT:
    """Narrow one validated parameter block to the shape its kind declares."""
    if not isinstance(parameters, model):
        raise ValueError(  # noqa: TRY004 - surfaced as a pydantic validation error.
            f"indicator parameters must be {model.__name__}"
        )
    return parameters


def _period_of(parameters: IndicatorParameterBlock) -> int:
    """Return ``period`` from the plain block or the annualized historical-volatility block."""
    if isinstance(parameters, HistoricalVolatilityIndicatorParameters):
        return parameters.period
    return _parameters_as(parameters, IndicatorParameters).period


def _macd_warmup(parameters: IndicatorParameterBlock) -> int:
    """Bars before MACD/PPO signal and histogram are defined."""
    shaped = _parameters_as(parameters, MacdIndicatorParameters)
    return shaped.slow_period + shaped.signal_period - 1


def _bollinger_warmup(parameters: IndicatorParameterBlock) -> int:
    """Bars before Bollinger middle, bands, %B, and bandwidth are defined."""
    return _parameters_as(parameters, BollingerIndicatorParameters).period


def _stochastic_warmup(parameters: IndicatorParameterBlock) -> int:
    """Bars before stochastic %D is defined."""
    shaped = _parameters_as(parameters, StochasticIndicatorParameters)
    return shaped.k_period + shaped.d_period - 1


def _adx_warmup(parameters: IndicatorParameterBlock) -> int:
    """Bars before ADX is defined when every DX after DI warmup exists."""
    return 2 * _period_of(parameters) - 1


def _dema_warmup(parameters: IndicatorParameterBlock) -> int:
    """Bars before EMA(EMA) is seeded: two chained SMA-seeded EMAs."""
    return 2 * _period_of(parameters) - 1


def _tema_warmup(parameters: IndicatorParameterBlock) -> int:
    """Bars before the third chained SMA-seeded EMA is defined."""
    return 3 * _period_of(parameters) - 2


def _trix_warmup(parameters: IndicatorParameterBlock) -> int:
    """Bars before the triple EMA has one previous value to difference against."""
    return 3 * _period_of(parameters) - 1


def _hma_warmup(parameters: IndicatorParameterBlock) -> int:
    """Bars before WMA(raw, floor(sqrt(period))) is defined over the raw Hull series."""
    period = _period_of(parameters)
    return period + isqrt(period) - 1


def _kama_warmup(parameters: IndicatorParameterBlock) -> int:
    """Bars before the first efficiency ratio (``period`` changes) updates the seed."""
    return _parameters_as(parameters, KamaIndicatorParameters).period + 1


def _supertrend_warmup(parameters: IndicatorParameterBlock) -> int:
    """Bars before the Wilder ATR seeds the first band."""
    return _parameters_as(parameters, SupertrendIndicatorParameters).period


def _parabolic_sar_warmup(parameters: IndicatorParameterBlock) -> int:
    """Bars before the first SAR: one prior bar decides the initial trend."""
    _parameters_as(parameters, ParabolicSarIndicatorParameters)
    return 2


def _ichimoku_warmup(parameters: IndicatorParameterBlock) -> int:
    """Bars before the longest midpoint window (leading span B) is complete."""
    shaped = _parameters_as(parameters, IchimokuIndicatorParameters)
    return max(shaped.tenkan_period, shaped.kijun_period, shaped.senkou_b_period)


def _stochastic_rsi_warmup(parameters: IndicatorParameterBlock) -> int:
    """Bars before %D: RSI changes, stochastic window, then both SMA smoothings."""
    shaped = _parameters_as(parameters, StochasticRsiIndicatorParameters)
    return shaped.rsi_period + shaped.stoch_period + shaped.k_period + shaped.d_period - 2


def _ultimate_oscillator_warmup(parameters: IndicatorParameterBlock) -> int:
    """Bars before the long window holds ``long_period`` previous-close comparisons."""
    return _parameters_as(parameters, UltimateOscillatorIndicatorParameters).long_period + 1


def _awesome_oscillator_warmup(parameters: IndicatorParameterBlock) -> int:
    """Bars before the slow median-price SMA is defined."""
    return _parameters_as(parameters, AwesomeOscillatorIndicatorParameters).slow_period


def _tsi_warmup(parameters: IndicatorParameterBlock) -> int:
    """Bars before the TSI signal line: one change, two EMAs, then the signal EMA."""
    shaped = _parameters_as(parameters, TsiIndicatorParameters)
    return shaped.long_period + shaped.short_period + shaped.signal_period - 1


def _keltner_warmup(parameters: IndicatorParameterBlock) -> int:
    """Bars before both the EMA middle and the Wilder ATR are defined."""
    shaped = _parameters_as(parameters, KeltnerIndicatorParameters)
    return max(shaped.period, shaped.atr_period)


def _historical_volatility_warmup(parameters: IndicatorParameterBlock) -> int:
    """Bars before ``period`` log returns exist."""
    return _period_of(parameters) + 1


def _signal_line_warmup(parameters: IndicatorParameterBlock) -> int:
    """Bars before the signal SMA of a cumulative series is defined."""
    return _parameters_as(parameters, SignalLineIndicatorParameters).signal_period


_WARMUP_RULES: dict[IndicatorKind, Callable[[IndicatorParameterBlock], int]] = {
    IndicatorKind.MACD: _macd_warmup,
    IndicatorKind.PPO: _macd_warmup,
    IndicatorKind.BOLLINGER: _bollinger_warmup,
    IndicatorKind.BOLLINGER_PERCENT_B: _bollinger_warmup,
    IndicatorKind.BOLLINGER_BANDWIDTH: _bollinger_warmup,
    IndicatorKind.STOCHASTIC: _stochastic_warmup,
    IndicatorKind.ADX: _adx_warmup,
    IndicatorKind.DEMA: _dema_warmup,
    IndicatorKind.TEMA: _tema_warmup,
    IndicatorKind.TRIX: _trix_warmup,
    IndicatorKind.HMA: _hma_warmup,
    IndicatorKind.KAMA: _kama_warmup,
    IndicatorKind.SUPERTREND: _supertrend_warmup,
    IndicatorKind.PARABOLIC_SAR: _parabolic_sar_warmup,
    IndicatorKind.ICHIMOKU: _ichimoku_warmup,
    IndicatorKind.STOCHASTIC_RSI: _stochastic_rsi_warmup,
    IndicatorKind.ULTIMATE_OSCILLATOR: _ultimate_oscillator_warmup,
    IndicatorKind.AWESOME_OSCILLATOR: _awesome_oscillator_warmup,
    IndicatorKind.TSI: _tsi_warmup,
    IndicatorKind.KELTNER: _keltner_warmup,
    IndicatorKind.HISTORICAL_VOLATILITY: _historical_volatility_warmup,
    IndicatorKind.OBV: _signal_line_warmup,
    IndicatorKind.ACCUMULATION_DISTRIBUTION: _signal_line_warmup,
}
"""Warmup formulas for kinds that are not ``period`` (plus one for lookback kinds)."""
