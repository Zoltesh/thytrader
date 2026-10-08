"""Data model of the indicator catalog and the helpers that build its entries.

Categories, parameter and kind specs, input and parameter-kind literals, and the
``_period``, ``_integer``, ``_multiplier``, ``_configurable``, and ``_locked`` builders
the per-category spec modules use. The assembled catalog is
:mod:`thytrader.strategies.indicator_catalog`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from thytrader.strategies.models import (
    IndicatorKind,
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
