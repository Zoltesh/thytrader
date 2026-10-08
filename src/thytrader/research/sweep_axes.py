"""Typed parameter-sweep axes, their fail-closed target rules, and Cartesian grid expansion."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from itertools import product
import re
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from thytrader.evaluation.models import DecimalInputText

_MAX_AXES = 4
_MAX_VALUES_PER_AXIS = 8
MAX_SYNC_CANDIDATES = 8
"""Candidate cap for a synchronous (HTTP 201) sweep or WFO submit."""
MAX_CANDIDATES = 64
"""Absolute candidate cap; grids above ``MAX_SYNC_CANDIDATES`` run only as async jobs."""
_INDICATOR_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_INTEGER_INDICATOR_PARAMETERS = frozenset(
    {
        "period",
        "fast_period",
        "slow_period",
        "signal_period",
        "k_period",
        "d_period",
        "atr_period",
        "tenkan_period",
        "kijun_period",
        "senkou_b_period",
        "rsi_period",
        "stoch_period",
        "short_period",
        "medium_period",
        "long_period",
        "annualization_periods",
    }
)
_INTEGER_PARAMETERS = _INTEGER_INDICATOR_PARAMETERS | {
    "offset",
    "max_bars_held",
    "max_entry_wait_bars",
}
_DECIMAL_INDICATOR_PARAMETERS = frozenset(
    {"stdev_multiplier", "multiplier", "step", "max_step", "value"}
)
_OFFSET_PARAMETER = "offset"
"""Indicator-level bar lag: written to the declaration, not to ``parameters``."""
_INDICATOR_PARAMETERS = (
    _INTEGER_INDICATOR_PARAMETERS | _DECIMAL_INDICATOR_PARAMETERS | {_OFFSET_PARAMETER}
)
_SIZING_PARAMETERS = frozenset({"risk_fraction", "min_quote_notional", "max_quote_notional"})
_EXITS_PARAMETERS = frozenset(
    {
        "initial_stop_multiple",
        "take_profit_multiple",
        "trailing_stop_multiple",
        "max_bars_held",
    }
)
_EXECUTION_PARAMETERS = frozenset({"max_entry_wait_bars"})
_LITERAL_PARAMETERS = frozenset({"literal"})
_CONDITION_OPERATORS = frozenset(
    {
        "greater_than",
        "greater_than_or_equal",
        "less_than",
        "less_than_or_equal",
        "equals",
    }
)
ALLOWED_PARAMETERS = _INDICATOR_PARAMETERS


class SweepAxisTarget(StrEnum):
    """Where one sweep axis writes. Product and timeframe are not sweepable."""

    INDICATOR = "indicator"
    SIZING = "sizing"
    EXITS = "exits"
    EXECUTION = "execution"
    ENTRY_LITERAL = "entry_literal"
    HTF_LITERAL = "htf_literal"


class ParameterAxis(BaseModel):
    """One discrete sweep axis. Default target is indicator (omitted from JSON)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    target: SweepAxisTarget = Field(
        default=SweepAxisTarget.INDICATOR,
        exclude_if=lambda value: value is SweepAxisTarget.INDICATOR,
    )
    indicator_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    parameter: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    values: tuple[DecimalInputText, ...] = Field(
        min_length=2,
        max_length=_MAX_VALUES_PER_AXIS,
        description=(
            "2-8 unique values on this axis. Combined with other axes, the Cartesian product "
            f"must be at most {MAX_CANDIDATES} candidates ({MAX_SYNC_CANDIDATES} for a "
            "synchronous submit; larger grids run as async jobs)."
        ),
    )
    condition_operator: str | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("values")
    @classmethod
    def require_unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Keep the Cartesian grid fail-closed and reproducible."""
        if len(value) != len(set(value)):
            raise ValueError("parameter_axes.values must be unique")
        return value

    @model_validator(mode="after")
    def validate_target_shape(self) -> Self:
        """Require locator fields that match the axis target without rewriting logic."""
        self._require_parameter_for_target()
        self._require_locator_for_target()
        if self.condition_operator is not None:
            if self.target not in {SweepAxisTarget.ENTRY_LITERAL, SweepAxisTarget.HTF_LITERAL}:
                raise ValueError("condition_operator is only valid on literal sweep axes")
            if self.condition_operator not in _CONDITION_OPERATORS:
                raise ValueError("condition_operator must be a non-crossover comparison operator")
        return self

    def _require_parameter_for_target(self) -> None:
        """Reject parameter names the chosen target does not declare."""
        allowed = _parameters_for_target(self.target)
        if self.parameter not in allowed:
            raise ValueError(_parameter_error(self.target))

    def _require_locator_for_target(self) -> None:
        """Require indicator_id only when the target addresses an indicator or literal."""
        needs_indicator = self.target in {
            SweepAxisTarget.INDICATOR,
            SweepAxisTarget.ENTRY_LITERAL,
            SweepAxisTarget.HTF_LITERAL,
        }
        if needs_indicator:
            if self.indicator_id is None or not _INDICATOR_ID_PATTERN.fullmatch(self.indicator_id):
                raise ValueError("parameter_axes.indicator_id is required for this target")
            return
        if self.indicator_id is not None:
            raise ValueError("parameter_axes.indicator_id is not valid for this target")


@dataclass(frozen=True, slots=True)
class AxisCell:
    """One Cartesian assignment used to derive a candidate document."""

    target: SweepAxisTarget
    locator: str
    parameter: str
    value: str
    condition_operator: str | None = None

    def identity_tuple(self) -> tuple[str, str, str]:
        """Return the fingerprint cell. Indicator-only grids keep the ADR 0044 3-tuple."""
        if self.target is SweepAxisTarget.INDICATOR and self.condition_operator is None:
            return (self.locator, self.parameter, self.value)
        return (
            f"{self.target.value}:{self.locator}:{self.condition_operator or ''}",
            self.parameter,
            self.value,
        )


def parameter_axes_candidate_count(axes: tuple[ParameterAxis, ...]) -> int:
    """Return the Cartesian product size for one parameter grid."""
    count = 1
    for axis in axes:
        count *= len(axis.values)
    return count


def validate_parameter_axes_candidate_budget(axes: tuple[ParameterAxis, ...]) -> None:
    """Reject grids whose Cartesian product exceeds the absolute (async) candidate cap.

    The tighter synchronous cap is a property of how the study runs, not of the
    request, so :func:`thytrader.research.studies.plan_study` enforces it.
    """
    if not axes:
        raise ValueError("parameter_axes is required")
    if len(axes) > _MAX_AXES:
        raise ValueError("parameter_axes accepts at most 4 axes")
    keys = [
        (axis.target, axis.indicator_id or "", axis.parameter, axis.condition_operator or "")
        for axis in axes
    ]
    if len(keys) != len(set(keys)):
        raise ValueError("parameter_axes must use distinct target, locator, and parameter tuples")
    count = parameter_axes_candidate_count(axes)
    if count > MAX_CANDIDATES:
        raise ValueError(
            f"parameter_axes Cartesian product yields {count} candidates; "
            f"at most {MAX_CANDIDATES} are allowed, and at most {MAX_SYNC_CANDIDATES} "
            "without --async (≤8 values per axis does not imply a small total)"
        )


def expand_parameter_grid(axes: tuple[ParameterAxis, ...]) -> tuple[tuple[AxisCell, ...], ...]:
    """Return Cartesian cells in axis declaration order."""
    validate_parameter_axes_candidate_budget(axes)
    return tuple(
        tuple(_cell_for_axis(axes[index], value) for index, value in enumerate(combo))
        for combo in product(*(axis.values for axis in axes))
    )


def _cell_for_axis(axis: ParameterAxis, value: str) -> AxisCell:
    """Bind one axis value into a Cartesian cell."""
    return AxisCell(
        target=axis.target,
        locator=axis.indicator_id or "",
        parameter=axis.parameter,
        value=value,
        condition_operator=axis.condition_operator,
    )


def _parameters_for_target(target: SweepAxisTarget) -> frozenset[str]:
    """Return the fail-closed parameter names legal on one axis target."""
    if target is SweepAxisTarget.INDICATOR:
        return _INDICATOR_PARAMETERS
    if target is SweepAxisTarget.SIZING:
        return _SIZING_PARAMETERS
    if target is SweepAxisTarget.EXITS:
        return _EXITS_PARAMETERS
    if target is SweepAxisTarget.EXECUTION:
        return _EXECUTION_PARAMETERS
    return _LITERAL_PARAMETERS


def _parameter_error(target: SweepAxisTarget) -> str:
    """Explain which parameter names a target accepts."""
    names = ", ".join(sorted(_parameters_for_target(target)))
    return f"parameter_axes.parameter must be one of: {names}"


AxisValue = int | str
"""One sweep coordinate as the strategy document carries it (an int period or a decimal)."""
