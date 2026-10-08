"""Entry rules, same-side pyramiding, and the optional higher-timeframe filter."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from thytrader.market_data.models import DatasetTimeframe
from thytrader.strategies.economic_guard import EconomicEntryGuard
from thytrader.strategies.schema.conditions import (
    ConditionGroup,
    _referenced_indicator_ids,
    _require_bounded_condition_tree,
    _require_condition_series,
    condition_indicator_operands,
)
from thytrader.strategies.schema.indicator_definition import (
    IndicatorDefinition,
    _indicator_input_fields,
)
from thytrader.strategies.schema.market import DataRequirements
from thytrader.strategies.schema.primitives import _FrozenModel
from thytrader.strategies.schema.timeframes import extra_indicator_timeframe_warmup


class IntraStrategyPyramiding(_FrozenModel):
    """Opt-in same-side adds onto one open position. Averaging down is rejected."""

    enabled: Literal[True]
    require_unrealized_profit: Literal[True] = True


class EntryDefinition(_FrozenModel):
    """Define conservative long or short entry intent, cooldown, and optional pyramiding."""

    side: Literal["long", "short"]
    when: ConditionGroup
    cooldown_bars: int = Field(ge=0, le=10_000)
    max_open_positions: int = Field(ge=1, le=8)
    economic_guard: EconomicEntryGuard | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    pyramiding: IntraStrategyPyramiding | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def validate_condition_complexity(self) -> Self:
        """Reject condition trees whose bounded grammar could exhaust consumers."""
        _require_bounded_condition_tree(self.when)
        return self

    @model_validator(mode="after")
    def validate_pyramiding_bounds(self) -> Self:
        """Require max_open_positions=1 unless pyramiding is explicitly enabled."""
        if self.pyramiding is None:
            if self.max_open_positions != 1:
                raise ValueError("max_open_positions must be 1 unless pyramiding is enabled")
            return self
        if self.max_open_positions < 2:
            raise ValueError("enabled pyramiding requires max_open_positions between 2 and 8")
        return self


class HigherTimeframeFilter(_FrozenModel):
    """Optional closed-bar HTF filter AND-ed with LTF entry on the decision clock."""

    timeframe: DatasetTimeframe
    data_requirements: DataRequirements
    indicators: tuple[IndicatorDefinition, ...] = Field(min_length=1, max_length=20)
    when: ConditionGroup

    @model_validator(mode="after")
    def validate_htf_semantics(self) -> Self:
        """Resolve HTF indicator identity, warmup, and bounded condition grammar."""
        identifiers = [indicator.id for indicator in self.indicators]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("HTF indicator ids must be unique")
        known = set(identifiers)
        unknown = _referenced_indicator_ids(self.when) - known
        if unknown:
            raise ValueError(f"unknown HTF indicator references: {sorted(unknown)}")
        if any(indicator.timeframe is not None for indicator in self.indicators):
            raise ValueError("HTF indicators must omit timeframe")
        if any(indicator.source is not None for indicator in self.indicators):
            raise ValueError(
                "HTF indicators must omit source: the HTF filter reads the traded instrument; "
                "gate on a reference instrument in entry.when instead"
            )
        if self.data_requirements.reference_instruments:
            raise ValueError(
                "htf_filter.data_requirements must not declare reference_instruments; declare "
                "them in the top-level data_requirements"
            )
        _require_condition_series(self.when, self.indicators)
        required_fields = {
            field for indicator in self.indicators for field in _indicator_input_fields(indicator)
        }
        if not required_fields.issubset(self.data_requirements.required_fields):
            raise ValueError("HTF required_fields must include every HTF indicator input")
        required_warmup = extra_indicator_timeframe_warmup(
            self.indicators, operands=condition_indicator_operands(self.when)
        )
        if self.data_requirements.warmup_bars < required_warmup:
            raise ValueError("HTF warmup_bars must cover the longest HTF indicator period")
        _require_bounded_condition_tree(self.when)
        return self
