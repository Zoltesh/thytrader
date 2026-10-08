"""Shared indicator-engine helpers: the calculation error, source series, and parameters."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.strategies.models import HistoricalVolatilityIndicatorParameters, IndicatorParameters

if TYPE_CHECKING:
    from collections.abc import Sequence
    from decimal import Decimal

    from pydantic import BaseModel

    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import IndicatorDefinition


class IndicatorCalculationError(ValueError):
    """Report unsupported or invalid deterministic indicator input."""


def _locked_source_series(
    indicator: IndicatorDefinition,
    candles: Sequence[Candle],
) -> tuple[Decimal, ...]:
    """Return the single OHLCV field selected by one identity or locked source kind."""
    source = indicator.input
    if source == "open":
        return tuple(candle.open for candle in candles)
    if source == "close":
        return tuple(candle.close for candle in candles)
    if source == "volume":
        return tuple(candle.volume for candle in candles)
    if source == "high":
        return tuple(candle.high for candle in candles)
    if source == "low":
        return tuple(candle.low for candle in candles)
    raise IndicatorCalculationError(
        f"Indicator kind {indicator.kind.value} is not implemented by this engine contract."
    )


def _parameters_of[ParametersT: BaseModel](
    indicator: IndicatorDefinition,
    model: type[ParametersT],
) -> ParametersT:
    """Return the validated parameter block, or fail closed when the shape is unexpected."""
    parameters = indicator.parameters
    if not isinstance(parameters, model):
        raise IndicatorCalculationError(
            f"Indicator kind {indicator.kind.value} is not implemented by this engine contract."
        )
    return parameters


def _period_parameter(indicator: IndicatorDefinition) -> int:
    """Return ``period`` from a plain or annualized-volatility parameter block."""
    parameters = indicator.parameters
    if isinstance(parameters, HistoricalVolatilityIndicatorParameters):
        return parameters.period
    return _parameters_of(indicator, IndicatorParameters).period


def _windows_defined(
    values: Sequence[Decimal | None],
    period: int,
) -> tuple[tuple[Decimal, ...] | None, ...]:
    """Return each inclusive window when it is complete and fully defined, else ``None``."""
    windows: list[tuple[Decimal, ...] | None] = []
    for index in range(len(values)):
        if index + 1 < period:
            windows.append(None)
            continue
        window = values[index + 1 - period : index + 1]
        defined = tuple(value for value in window if value is not None)
        windows.append(defined if len(defined) == period else None)
    return tuple(windows)


def _difference(
    left: Sequence[Decimal | None],
    right: Sequence[Decimal | None],
) -> tuple[Decimal | None, ...]:
    """Return ``left - right`` per bar, undefined when either side is undefined."""
    return tuple(
        None if first is None or second is None else first - second
        for first, second in zip(left, right, strict=True)
    )
