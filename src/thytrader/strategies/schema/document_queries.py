"""Read-only queries derived from a validated strategy document.

Covered instruments and pyramiding, reference-instrument groups and their data
requirements, extra indicator timeframes, expanded data requirements, and the indicator
operands and value keys used for calculation, warmup, and traces.
``thytrader.strategies.models`` re-exports every public name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from thytrader.market_data.models import EXECUTION_TIMEFRAMES
from thytrader.strategies.schema.conditions import (
    ConditionNode,
    IndicatorOperand,
    condition_indicator_operands,
    operand_value_key,
)
from thytrader.strategies.schema.exits import (
    signal_exit_condition,
)
from thytrader.strategies.schema.indicator_definition import (
    IndicatorDefinition,
    indicator_value_keys,
)
from thytrader.strategies.schema.market import (
    Instrument,
    ReferenceDataRequirement,
    ReferenceInstrument,
    TimeframeDataRequirement,
)
from thytrader.strategies.schema.timeframes import (
    extra_indicator_required_fields,
    extra_indicator_timeframe_warmup,
    resolved_indicator_timeframe,
)

if TYPE_CHECKING:
    from decimal import Decimal

    from thytrader.strategies.models import StrategyDefinition


MAX_STRATEGY_INSTRUMENTS = 8


def covered_instruments(definition: StrategyDefinition) -> tuple[Instrument, ...]:
    """Return the primary instrument followed by additional instruments."""
    return (definition.instrument, *definition.additional_instruments)


def covered_product_ids(definition: StrategyDefinition) -> tuple[str, ...]:
    """Return covered Coinbase USD spot product ids in document order."""
    return tuple(item.product_id for item in covered_instruments(definition))


def lockstep_product_ids(definition: StrategyDefinition) -> tuple[str, ...]:
    """Return covered product ids in lexicographic order for shared-bar evaluation."""
    return tuple(sorted(covered_product_ids(definition)))


def pyramiding_enabled(definition: StrategyDefinition) -> bool:
    """True when the document explicitly opts into same-side adds."""
    return definition.entry.pyramiding is not None


def can_pyramid_add(
    *,
    strategy: StrategyDefinition,
    side: Literal["long", "short"],
    entry_price: Decimal,
    mark: Decimal,
    add_count: int,
) -> bool:
    """Return whether one same-side add is legal under schema (not the runtime risk policy)."""
    policy = strategy.entry.pyramiding
    if policy is None or add_count < 1 or add_count >= strategy.entry.max_open_positions:
        return False
    if side == "long":
        return mark > entry_price
    return mark < entry_price


def decision_clock_indicators(definition: StrategyDefinition) -> tuple[IndicatorDefinition, ...]:
    """Return LTF-list indicators that evaluate on the traded instrument's decision clock.

    Indicators with a reference ``source`` read another instrument's bars and are
    excluded (see :func:`reference_indicator_groups`).
    """
    return tuple(
        indicator
        for indicator in definition.indicators
        if indicator.source is None
        and resolved_indicator_timeframe(indicator, definition.timeframe) == definition.timeframe
    )


def reference_instruments(definition: StrategyDefinition) -> tuple[ReferenceInstrument, ...]:
    """Return the declared read-only reference series in declaration order (ADR 0096)."""
    return definition.data_requirements.reference_instruments


def reference_indicator_groups(
    definition: StrategyDefinition,
) -> tuple[tuple[ReferenceInstrument, tuple[IndicatorDefinition, ...]], ...]:
    """Group indicators by the reference they read, in reference declaration order.

    Validation guarantees every reference has at least one indicator.
    """
    return tuple(
        (
            reference,
            tuple(
                indicator for indicator in definition.indicators if indicator.source == reference.id
            ),
        )
        for reference in reference_instruments(definition)
    )


def reference_data_requirements(
    definition: StrategyDefinition,
) -> tuple[ReferenceDataRequirement, ...]:
    """Return every reference series a run or deployment must load, with derived warmup."""
    return tuple(
        ReferenceDataRequirement(
            reference_id=reference.id,
            product_id=reference.product_id,
            timeframe=reference.timeframe,
            warmup_bars=extra_indicator_timeframe_warmup(
                indicators, operands=strategy_indicator_operands(definition)
            ),
            required_fields=extra_indicator_required_fields(indicators),
        )
        for reference, indicators in reference_indicator_groups(definition)
        if indicators
    )


def reference_series(definition: StrategyDefinition) -> frozenset[tuple[str, str]]:
    """Return the ``(product_id, timeframe)`` pairs the document reads as references."""
    return frozenset(
        (reference.product_id, reference.timeframe)
        for reference in reference_instruments(definition)
    )


def extra_indicator_timeframe_groups(
    definition: StrategyDefinition,
) -> tuple[tuple[str, tuple[IndicatorDefinition, ...]], ...]:
    """Group extra-TF LTF-list indicators by clock in venue-duration order."""
    grouped: dict[str, list[IndicatorDefinition]] = {}
    for indicator in definition.indicators:
        clock = resolved_indicator_timeframe(indicator, definition.timeframe)
        if clock == definition.timeframe:
            continue
        grouped.setdefault(clock, []).append(indicator)
    return tuple(
        (timeframe, tuple(grouped[timeframe]))
        for timeframe in EXECUTION_TIMEFRAMES
        if timeframe in grouped
    )


def extra_indicator_timeframes(definition: StrategyDefinition) -> tuple[str, ...]:
    """Return extra indicator clocks in venue-duration order."""
    groups = extra_indicator_timeframe_groups(definition)
    return tuple(timeframe for timeframe, _indicators in groups)


def unbound_indicator_timeframes(definition: StrategyDefinition) -> tuple[str, ...]:
    """Return extra indicator clocks that need their own research dataset fingerprint.

    An extra TF that equals ``htf_filter.timeframe`` is covered by ``htf_dataset_fingerprint``.
    """
    htf_timeframe = definition.htf_filter.timeframe if definition.htf_filter is not None else None
    return tuple(
        timeframe
        for timeframe in extra_indicator_timeframes(definition)
        if timeframe != htf_timeframe
    )


def expanded_data_requirements(
    definition: StrategyDefinition,
) -> tuple[TimeframeDataRequirement, ...]:
    """Return every timeframe a research run must fingerprint and bind."""
    requirements: list[TimeframeDataRequirement] = [
        TimeframeDataRequirement(
            timeframe=definition.timeframe,
            warmup_bars=definition.data_requirements.warmup_bars,
            required_fields=definition.data_requirements.required_fields,
            role="decision",
        )
    ]
    htf_filter = definition.htf_filter
    if htf_filter is not None:
        requirements.append(
            TimeframeDataRequirement(
                timeframe=htf_filter.timeframe,
                warmup_bars=htf_filter.data_requirements.warmup_bars,
                required_fields=htf_filter.data_requirements.required_fields,
                role="filter",
            )
        )
    for timeframe, indicators in extra_indicator_timeframe_groups(definition):
        if htf_filter is not None and timeframe == htf_filter.timeframe:
            continue
        requirements.append(
            TimeframeDataRequirement(
                timeframe=timeframe,
                warmup_bars=extra_indicator_timeframe_warmup(
                    indicators, operands=strategy_indicator_operands(definition)
                ),
                required_fields=extra_indicator_required_fields(indicators),
                role="indicator",
            )
        )
    return tuple(requirements)


def strategy_indicator_operands(definition: StrategyDefinition) -> tuple[IndicatorOperand, ...]:
    """Collect entry, signal-exit, and filter operands for calculation and warmup."""
    conditions: list[ConditionNode] = [definition.entry.when]
    exit_condition = signal_exit_condition(definition.exits)
    if exit_condition is not None:
        conditions.append(exit_condition)
    if definition.htf_filter is not None:
        conditions.append(definition.htf_filter.when)
    return tuple(
        dict.fromkeys(
            operand for node in conditions for operand in condition_indicator_operands(node)
        )
    )


def strategy_indicator_value_keys(definition: StrategyDefinition) -> tuple[str, ...]:
    """Keep historical trace keys and append distinct lagged operand evidence keys."""
    keys = tuple(
        key
        for indicator in decision_and_filter_indicators(definition)
        for key in indicator_value_keys(indicator)
    )
    return keys + tuple(
        operand_value_key(operand)
        for operand in strategy_indicator_operands(definition)
        if operand.offset is not None
    )


def decision_and_filter_indicators(
    definition: StrategyDefinition,
) -> tuple[IndicatorDefinition, ...]:
    """Return LTF then HTF indicators in declaration order for traces and summaries."""
    htf_filter = definition.htf_filter
    if htf_filter is None:
        return definition.indicators
    return (*definition.indicators, *htf_filter.indicators)
