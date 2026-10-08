"""Indicator and literal operands, comparison conditions, and bounded rule trees."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Self, TypeAlias

from pydantic import Field, field_validator, model_validator

from thytrader.strategies.schema.indicator_definition import (
    MAX_INDICATOR_OFFSET,
    indicator_output_series,
)
from thytrader.strategies.schema.indicator_parameters import IndicatorKind
from thytrader.strategies.schema.primitives import DecimalText, _FrozenModel

if TYPE_CHECKING:
    from thytrader.strategies.schema.indicator_definition import IndicatorDefinition


_MAX_CONDITION_DEPTH = 4
_MAX_CONDITION_NODES = 64


class IndicatorOperand(_FrozenModel):
    """Reference a previously declared indicator, and a series when the kind is multi-output."""

    indicator: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    series: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]{0,31}$",
        exclude_if=lambda value: value is None,
    )
    offset: int | None = Field(
        default=None,
        ge=0,
        le=MAX_INDICATOR_OFFSET,
        exclude_if=lambda value: value is None,
        description="Additional lag in completed bars of the referenced indicator's own clock.",
        strict=True,
    )

    @field_validator("offset")
    @classmethod
    def normalize_zero_offset(cls, value: int | None) -> int | None:
        """Omit a zero lag so existing operands keep their canonical bytes."""
        return None if value == 0 else value


def operand_value_key(operand: IndicatorOperand) -> str:
    """Return the evaluator key addressed by one indicator operand."""
    key = operand.indicator if operand.series is None else f"{operand.indicator}.{operand.series}"
    return key if operand.offset is None else f"{key}@{operand.offset}"


def condition_indicator_operands(condition: ConditionNode) -> tuple[IndicatorOperand, ...]:
    """Return distinct indicator reads in a bounded rule tree, in encounter order."""
    return tuple(
        dict.fromkeys(
            operand
            for comparison in _comparison_conditions(condition)
            for operand in (comparison.left, comparison.right)
            if isinstance(operand, IndicatorOperand)
        )
    )


class LiteralOperand(_FrozenModel):
    """Represent one exact decimal literal used in a comparison."""

    literal: DecimalText


ConditionOperand = IndicatorOperand | LiteralOperand


class ComparisonOperator(StrEnum):
    """Pure comparison operations supported by the first reference profile."""

    GT = "greater_than"
    GTE = "greater_than_or_equal"
    LT = "less_than"
    LTE = "less_than_or_equal"
    EQ = "equals"
    CROSSES_ABOVE = "crosses_above"
    CROSSES_BELOW = "crosses_below"


class ComparisonCondition(_FrozenModel):
    """Compare two declarative operands without arbitrary expressions."""

    left: ConditionOperand
    operator: ComparisonOperator
    right: ConditionOperand

    @model_validator(mode="after")
    def validate_cross_operands(self) -> Self:
        """Require crossover operations to compare two indicator series."""
        if self.operator in {
            ComparisonOperator.CROSSES_ABOVE,
            ComparisonOperator.CROSSES_BELOW,
        } and not isinstance(self.left, IndicatorOperand):
            raise ValueError("crossover left operand must reference an indicator")
        if self.operator in {
            ComparisonOperator.CROSSES_ABOVE,
            ComparisonOperator.CROSSES_BELOW,
        } and not isinstance(self.right, IndicatorOperand):
            raise ValueError("crossover right operand must reference an indicator")
        return self


class AllCondition(_FrozenModel):
    """Require every bounded child condition in the group to be true."""

    all: tuple[ConditionNode, ...] = Field(min_length=1, max_length=20)


class AnyCondition(_FrozenModel):
    """Require at least one bounded child condition in the group to be true."""

    any: tuple[ConditionNode, ...] = Field(min_length=1, max_length=20)


class NotCondition(_FrozenModel):
    """Negate exactly one bounded child condition."""

    not_: ConditionNode = Field(alias="not", serialization_alias="not")


ConditionNode: TypeAlias = (  # noqa: UP040 - patch tooling must parse pre-3.12 syntax.
    ComparisonCondition | AllCondition | AnyCondition | NotCondition
)
ConditionGroup: TypeAlias = (  # noqa: UP040 - patch tooling must parse pre-3.12 syntax.
    AllCondition | AnyCondition | NotCondition
)
AllCondition.model_rebuild()
AnyCondition.model_rebuild()
NotCondition.model_rebuild()


def _comparison_conditions(condition: ConditionNode) -> tuple[ComparisonCondition, ...]:
    """Return every comparison leaf from one bounded declarative condition tree."""
    if isinstance(condition, ComparisonCondition):
        return (condition,)
    if isinstance(condition, NotCondition):
        return _comparison_conditions(condition.not_)
    children = condition.all if isinstance(condition, AllCondition) else condition.any
    return tuple(comparison for child in children for comparison in _comparison_conditions(child))


def _condition_tree_size(condition: ConditionNode) -> tuple[int, int]:
    """Return total node count and maximum depth for one condition tree."""
    if isinstance(condition, ComparisonCondition):
        return (1, 1)
    if isinstance(condition, NotCondition):
        child_nodes, child_depth = _condition_tree_size(condition.not_)
        return (child_nodes + 1, child_depth + 1)
    children = condition.all if isinstance(condition, AllCondition) else condition.any
    child_sizes = tuple(_condition_tree_size(child) for child in children)
    return (
        1 + sum(nodes for nodes, _depth in child_sizes),
        1 + max(depth for _nodes, depth in child_sizes),
    )


def _require_bounded_condition_tree(condition: ConditionGroup) -> None:
    """Reject condition trees whose bounded grammar could exhaust consumers."""
    node_count, depth = _condition_tree_size(condition)
    if depth > _MAX_CONDITION_DEPTH:
        raise ValueError(f"condition tree depth exceeds {_MAX_CONDITION_DEPTH}")
    if node_count > _MAX_CONDITION_NODES:
        raise ValueError(f"condition tree node count exceeds {_MAX_CONDITION_NODES}")


def _require_operand_series(operand: IndicatorOperand, indicator: IndicatorDefinition) -> None:
    """Require series on multi-output kinds and forbid it on single-output kinds."""
    if indicator.kind is IndicatorKind.CONSTANT and operand.offset is not None:
        raise ValueError("constant operand must omit offset")
    outputs = indicator_output_series(indicator.kind)
    if outputs is None:
        if operand.series is not None:
            raise ValueError(f"{indicator.kind.value} operand must omit series")
        return
    if operand.series not in outputs:
        raise ValueError(f"{indicator.kind.value} series must be one of {', '.join(outputs)}")


def _require_condition_series(
    condition: ConditionGroup,
    indicators: tuple[IndicatorDefinition, ...],
) -> None:
    """Resolve every indicator operand's series against the declared kind."""
    by_id = {indicator.id: indicator for indicator in indicators}
    for comparison in _comparison_conditions(condition):
        for operand in (comparison.left, comparison.right):
            if isinstance(operand, IndicatorOperand):
                _require_operand_series(operand, by_id[operand.indicator])


def _referenced_indicator_ids(condition: ConditionGroup) -> set[str]:
    """Return every indicator identifier referenced by one condition tree."""
    return {
        operand.indicator
        for comparison in _comparison_conditions(condition)
        for operand in (comparison.left, comparison.right)
        if isinstance(operand, IndicatorOperand)
    }
