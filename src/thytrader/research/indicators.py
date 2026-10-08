"""Deterministic Decimal indicator calculations for signal evaluation.

This module dispatches declared indicators through the ``_*_CALCULATORS`` tables to
the indicator math in ``thytrader.research.indicator_math``, split by family: ``core``
(``IndicatorCalculationError`` and shared helpers), ``moving_averages``,
``rolling_statistics``, ``momentum``, ``volatility``, ``trend``, and ``volume``.
"""

from __future__ import annotations

from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from typing import TYPE_CHECKING

from thytrader.decimal_text import canonical_decimal
from thytrader.research.indicator_math.core import IndicatorCalculationError, _locked_source_series
from thytrader.research.indicator_math.momentum import (
    _awesome_oscillator_values,
    _chande_momentum_oscillator,
    _commodity_channel_index,
    _macd_series,
    _momentum,
    _ppo_series,
    _rate_of_change,
    _relative_strength_index,
    _stochastic_rsi_series,
    _stochastic_series,
    _triple_exponential_rate,
    _true_strength_series,
    _ultimate_oscillator_values,
    _williams_percent_r,
)
from thytrader.research.indicator_math.moving_averages import (
    _double_exponential_moving_average,
    _exponential_moving_average,
    _hull_moving_average,
    _kaufman_adaptive_values,
    _simple_moving_average,
    _triple_exponential_moving_average,
    _weighted_moving_average,
)
from thytrader.research.indicator_math.rolling_statistics import (
    _linear_regression_series,
    _percent_rank,
    _rolling_highest,
    _rolling_lowest,
    _rolling_population_stdev,
    _rolling_sample_stdev,
    _rolling_zscore,
)
from thytrader.research.indicator_math.trend import (
    _adx_series,
    _aroon_series,
    _ichimoku_series,
    _parabolic_sar_values,
    _supertrend_series,
    _vortex_series,
)
from thytrader.research.indicator_math.volatility import (
    _average_true_range,
    _bollinger_bandwidth_values,
    _bollinger_percent_b_values,
    _bollinger_series,
    _choppiness_index,
    _donchian_series,
    _historical_volatility_values,
    _keltner_series,
    _normalized_average_true_range,
)
from thytrader.research.indicator_math.volume import (
    _accumulation_distribution_series,
    _chaikin_money_flow,
    _force_index,
    _money_flow_index,
    _on_balance_volume_series,
    _rolling_volume_weighted_average_price,
    _volume_weighted_moving_average,
)
from thytrader.strategies.models import (
    ConstantIndicatorParameters,
    IndicatorKind,
    IndicatorParameters,
    indicator_offset,
    indicator_output_series,
    indicator_value_keys,
    operand_value_key,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import IndicatorDefinition, IndicatorOperand

__all__ = [
    "IndicatorCalculationError",
    "calculate_indicator_rows",
    "canonical_decimal",
]

_ENGINE_CONTEXT = Context(
    prec=64,
    rounding=ROUND_HALF_EVEN,
    Emin=-6143,
    Emax=6144,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)


def calculate_indicator_rows(
    indicators: Sequence[IndicatorDefinition],
    candles: Sequence[Candle],
    *,
    operands: Sequence[IndicatorOperand] = (),
) -> tuple[dict[str, Decimal | None], ...]:
    """Calculate declared outputs and requested operand lags on their native closed bars."""
    rows = [dict[str, Decimal | None]() for _candle in candles]
    with localcontext(_ENGINE_CONTEXT):
        for indicator in indicators:
            keyed_rows = _keyed_indicator_values(indicator, candles)
            for row, keyed in zip(rows, keyed_rows, strict=True):
                row.update(keyed)
    known = {indicator.id for indicator in indicators}
    for operand in operands:
        offset = operand.offset
        if offset is None or operand.indicator not in known:
            continue
        base_key = operand_value_key(operand.model_copy(update={"offset": None}))
        key = operand_value_key(operand)
        for index, row in enumerate(rows):
            row[key] = None if index < offset else rows[index - offset].get(base_key)
    return tuple(rows)


def _keyed_indicator_values(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> tuple[dict[str, Decimal | None], ...]:
    """Return one mapping of output keys to values for every supplied candle.

    A declared ``offset`` shifts every output series ``offset`` completed bars later:
    row ``i`` carries the unlagged row ``i - offset`` and the first ``offset`` rows are
    undefined. Only earlier bars are read, so a lag can never look ahead.
    """
    rows = _unlagged_keyed_values(indicator, candles)
    offset = indicator_offset(indicator)
    if offset == 0:
        return rows
    lead = min(offset, len(rows))
    undefined = dict.fromkeys(indicator_value_keys(indicator))
    return tuple(dict(undefined) for _index in range(lead)) + rows[: len(rows) - lead]


def _unlagged_keyed_values(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> tuple[dict[str, Decimal | None], ...]:
    """Return keyed values for every candle before any declared bar lag is applied."""
    series = indicator_output_series(indicator.kind)
    if series is None:
        values = _indicator_values(indicator, candles)
        return tuple({indicator.id: value} for value in values)
    matrix = _multi_series_values(indicator, candles)
    return tuple(
        {f"{indicator.id}.{name}": matrix[name][index] for name in series}
        for index in range(len(candles))
    )


def _multi_series_values(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> dict[str, tuple[Decimal | None, ...]]:
    """Dispatch one multi-output definition to its exact implemented series."""
    calculator = _MULTI_SERIES_CALCULATORS.get(indicator.kind)
    if calculator is None:
        raise IndicatorCalculationError(
            f"Indicator kind {indicator.kind.value} is not implemented by this engine contract."
        )
    return calculator(indicator, candles)


def _indicator_values(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> tuple[Decimal | None, ...]:
    """Dispatch one declarative definition to its exact implemented calculation."""
    if indicator.kind is IndicatorKind.IDENTITY:
        return _identity_values(indicator, candles)
    if indicator.kind is IndicatorKind.CONSTANT:
        return _constant_values(indicator, candles)
    shaped_calculator = _SHAPED_CALCULATORS.get(indicator.kind)
    if shaped_calculator is not None:
        return shaped_calculator(indicator, candles)
    parameters = indicator.parameters
    if not isinstance(parameters, IndicatorParameters):
        raise IndicatorCalculationError(
            f"Indicator kind {indicator.kind.value} is not implemented by this engine contract."
        )
    period = parameters.period
    candle_calculator = _CANDLE_CALCULATORS.get(indicator.kind)
    if candle_calculator is not None:
        return candle_calculator(candles, period)
    series = _locked_source_series(indicator, candles)
    series_calculator = _SERIES_CALCULATORS.get(indicator.kind)
    if series_calculator is not None:
        return series_calculator(series, period)
    raise IndicatorCalculationError(
        f"Indicator kind {indicator.kind.value} is not implemented by this engine contract."
    )


def _identity_values(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> tuple[Decimal | None, ...]:
    """Return the selected OHLCV field on every completed bar."""
    return _locked_source_series(indicator, candles)


def _constant_values(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> tuple[Decimal | None, ...]:
    """Repeat the declared finite level on every completed bar."""
    parameters = indicator.parameters
    if not isinstance(parameters, ConstantIndicatorParameters):
        raise IndicatorCalculationError(
            f"Indicator kind {indicator.kind.value} is not implemented by this engine contract."
        )
    value = Decimal(parameters.value)
    return tuple(value for _candle in candles)


_CANDLE_CALCULATORS: dict[
    IndicatorKind,
    Callable[[Sequence[Candle], int], tuple[Decimal | None, ...]],
] = {
    IndicatorKind.ATR: _average_true_range,
    IndicatorKind.WILLIAMS_R: _williams_percent_r,
    IndicatorKind.CCI: _commodity_channel_index,
    IndicatorKind.MFI: _money_flow_index,
    IndicatorKind.VWMA: _volume_weighted_moving_average,
    IndicatorKind.NATR: _normalized_average_true_range,
    IndicatorKind.CHOPPINESS: _choppiness_index,
    IndicatorKind.CMF: _chaikin_money_flow,
    IndicatorKind.VWAP: _rolling_volume_weighted_average_price,
    IndicatorKind.FORCE_INDEX: _force_index,
}
"""Period kinds that read whole candles (more than one OHLCV field)."""

_SERIES_CALCULATORS: dict[
    IndicatorKind,
    Callable[[Sequence[Decimal], int], tuple[Decimal | None, ...]],
] = {
    IndicatorKind.SMA: _simple_moving_average,
    IndicatorKind.VOLUME_SMA: _simple_moving_average,
    IndicatorKind.EMA: _exponential_moving_average,
    IndicatorKind.RSI: _relative_strength_index,
    IndicatorKind.HIGHEST: _rolling_highest,
    IndicatorKind.LOWEST: _rolling_lowest,
    IndicatorKind.STDEV: _rolling_population_stdev,
    IndicatorKind.STDEV_SAMPLE: _rolling_sample_stdev,
    IndicatorKind.ROC: _rate_of_change,
    IndicatorKind.WMA: _weighted_moving_average,
    IndicatorKind.MOMENTUM: _momentum,
    IndicatorKind.DEMA: _double_exponential_moving_average,
    IndicatorKind.TEMA: _triple_exponential_moving_average,
    IndicatorKind.HMA: _hull_moving_average,
    IndicatorKind.TRIX: _triple_exponential_rate,
    IndicatorKind.CMO: _chande_momentum_oscillator,
    IndicatorKind.ZSCORE: _rolling_zscore,
    IndicatorKind.PERCENT_RANK: _percent_rank,
}
"""Period kinds over one selected (or locked) OHLCV field."""

_SHAPED_CALCULATORS: dict[
    IndicatorKind,
    Callable[[IndicatorDefinition, Sequence[Candle]], tuple[Decimal | None, ...]],
] = {
    IndicatorKind.KAMA: _kaufman_adaptive_values,
    IndicatorKind.PARABOLIC_SAR: _parabolic_sar_values,
    IndicatorKind.ULTIMATE_OSCILLATOR: _ultimate_oscillator_values,
    IndicatorKind.AWESOME_OSCILLATOR: _awesome_oscillator_values,
    IndicatorKind.BOLLINGER_PERCENT_B: _bollinger_percent_b_values,
    IndicatorKind.BOLLINGER_BANDWIDTH: _bollinger_bandwidth_values,
    IndicatorKind.HISTORICAL_VOLATILITY: _historical_volatility_values,
}
"""Single-output kinds whose parameters are not the plain ``{period}`` block."""

_MULTI_SERIES_CALCULATORS: dict[
    IndicatorKind,
    Callable[[IndicatorDefinition, Sequence[Candle]], dict[str, tuple[Decimal | None, ...]]],
] = {
    IndicatorKind.MACD: _macd_series,
    IndicatorKind.BOLLINGER: _bollinger_series,
    IndicatorKind.STOCHASTIC: _stochastic_series,
    IndicatorKind.ADX: _adx_series,
    IndicatorKind.SUPERTREND: _supertrend_series,
    IndicatorKind.AROON: _aroon_series,
    IndicatorKind.ICHIMOKU: _ichimoku_series,
    IndicatorKind.VORTEX: _vortex_series,
    IndicatorKind.LINEAR_REGRESSION: _linear_regression_series,
    IndicatorKind.STOCHASTIC_RSI: _stochastic_rsi_series,
    IndicatorKind.PPO: _ppo_series,
    IndicatorKind.TSI: _true_strength_series,
    IndicatorKind.KELTNER: _keltner_series,
    IndicatorKind.DONCHIAN: _donchian_series,
    IndicatorKind.OBV: _on_balance_volume_series,
    IndicatorKind.ACCUMULATION_DISTRIBUTION: _accumulation_distribution_series,
}
"""Multi-series kinds; output names come from ``indicator_output_series``."""
