"""Canonical declarative strategy definitions and immutable identity helpers.

The schema is split by document section in ``thytrader.strategies.schema``
(``primitives``, ``market``, ``indicator_parameters``, ``indicator_definition``,
``indicator_warmup``, ``conditions``, ``timeframes``, ``entry``, ``exits``,
``execution``). This module defines the top-level ``StrategyDefinition`` with its sizing
and portfolio limits, cross-section validation, derived data requirements, and the
canonical bytes and fingerprint, and re-exports every public name of the section
modules (``__all__``), so it stays the import path. ``PortfolioLimits`` is defined here
because OpenAPI names it after this module (``thytrader__strategies__models__PortfolioLimits``,
disambiguating the portfolio model of the same name).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from typing import Literal, Self

from pydantic import Field, field_serializer, field_validator, model_validator

from thytrader.market_data.models import EXECUTION_TIMEFRAMES, DatasetTimeframe
from thytrader.strategies.schema.conditions import (
    AllCondition,
    AnyCondition,
    ComparisonCondition,
    ComparisonOperator,
    ConditionGroup,
    ConditionNode,
    ConditionOperand,
    IndicatorOperand,
    LiteralOperand,
    NotCondition,
    _referenced_indicator_ids,
    _require_condition_series,
    condition_indicator_operands,
    operand_value_key,
)
from thytrader.strategies.schema.entry import (
    EntryDefinition,
    HigherTimeframeFilter,
    IntraStrategyPyramiding,
)
from thytrader.strategies.schema.execution import ExecutionPreferences, StrategyMetadata
from thytrader.strategies.schema.exits import (
    AtrMultipleStop,
    AtrTrailingStop,
    DisabledTrailingStop,
    ExitDefinition,
    NoTakeProfit,
    RewardRiskTakeProfit,
    SignalExit,
    TakeProfitDefinition,
    TimeExit,
    TrailingStopDefinition,
    atr_trailing_stop,
    reward_risk_multiple,
    signal_exit_condition,
)
from thytrader.strategies.schema.indicator_definition import (
    ADX_OUTPUT_SERIES,
    BOLLINGER_OUTPUT_SERIES,
    CHANNEL_OUTPUT_SERIES,
    CLOSE_VOLUME_INPUT,
    HL_INPUT,
    HLC_INPUT,
    HLCV_INPUT,
    MACD_OUTPUT_SERIES,
    MAX_INDICATOR_OFFSET,
    STOCHASTIC_OUTPUT_SERIES,
    IndicatorDefinition,
    _indicator_input_fields,
    indicator_offset,
    indicator_output_series,
    indicator_value_keys,
)
from thytrader.strategies.schema.indicator_parameters import (
    AwesomeOscillatorIndicatorParameters,
    BollingerIndicatorParameters,
    ConstantIndicatorParameters,
    EmptyIndicatorParameters,
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
from thytrader.strategies.schema.indicator_warmup import indicator_min_warmup
from thytrader.strategies.schema.market import (
    MAX_REFERENCE_INSTRUMENTS,
    REFERENCE_ID_PATTERN,
    DataRequirements,
    Instrument,
    ReferenceDataRequirement,
    ReferenceInstrument,
    TimeframeDataRequirement,
)
from thytrader.strategies.schema.primitives import DecimalText, Uuid7, _FrozenModel
from thytrader.strategies.schema.timeframes import (
    STRATEGY_DECISION_TIMEFRAMES,
    STRATEGY_HTF_TIMEFRAMES,
    extra_indicator_required_fields,
    extra_indicator_timeframe_warmup,
    is_valid_htf_pair,
    is_valid_reference_pair,
    resolved_indicator_timeframe,
    timeframe_seconds,
)

__all__ = [
    "ADX_OUTPUT_SERIES",
    "BOLLINGER_OUTPUT_SERIES",
    "CHANNEL_OUTPUT_SERIES",
    "CLOSE_VOLUME_INPUT",
    "HLCV_INPUT",
    "HLC_INPUT",
    "HL_INPUT",
    "LEGACY_LIFECYCLE_KEYS",
    "MACD_OUTPUT_SERIES",
    "MAX_INDICATOR_OFFSET",
    "MAX_REFERENCE_INSTRUMENTS",
    "MAX_STRATEGY_INSTRUMENTS",
    "REFERENCE_ID_PATTERN",
    "STOCHASTIC_OUTPUT_SERIES",
    "STRATEGY_DECISION_TIMEFRAMES",
    "STRATEGY_HTF_TIMEFRAMES",
    "AllCondition",
    "AnyCondition",
    "AtrMultipleStop",
    "AtrTrailingStop",
    "AwesomeOscillatorIndicatorParameters",
    "BollingerIndicatorParameters",
    "ComparisonCondition",
    "ComparisonOperator",
    "ConditionGroup",
    "ConditionNode",
    "ConditionOperand",
    "ConstantIndicatorParameters",
    "DataRequirements",
    "DecimalText",
    "DisabledTrailingStop",
    "EmptyIndicatorParameters",
    "EntryDefinition",
    "ExecutionPreferences",
    "ExitDefinition",
    "HigherTimeframeFilter",
    "HistoricalVolatilityIndicatorParameters",
    "IchimokuIndicatorParameters",
    "IndicatorDefinition",
    "IndicatorKind",
    "IndicatorOperand",
    "IndicatorParameterBlock",
    "IndicatorParameters",
    "Instrument",
    "IntraStrategyPyramiding",
    "KamaIndicatorParameters",
    "KeltnerIndicatorParameters",
    "LiteralOperand",
    "MacdIndicatorParameters",
    "NoTakeProfit",
    "NotCondition",
    "ParabolicSarIndicatorParameters",
    "PortfolioLimits",
    "ReferenceDataRequirement",
    "ReferenceInstrument",
    "RewardRiskTakeProfit",
    "RiskFractionSizing",
    "SignalExit",
    "SignalLineIndicatorParameters",
    "StochasticIndicatorParameters",
    "StochasticRsiIndicatorParameters",
    "StrategyDefinition",
    "StrategyMetadata",
    "SupertrendIndicatorParameters",
    "TakeProfitDefinition",
    "TimeExit",
    "TimeframeDataRequirement",
    "TrailingStopDefinition",
    "TsiIndicatorParameters",
    "UltimateOscillatorIndicatorParameters",
    "Uuid7",
    "atr_trailing_stop",
    "can_pyramid_add",
    "canonical_strategy_bytes",
    "condition_indicator_operands",
    "covered_instruments",
    "covered_product_ids",
    "decision_and_filter_indicators",
    "decision_clock_indicators",
    "expanded_data_requirements",
    "extra_indicator_required_fields",
    "extra_indicator_timeframe_groups",
    "extra_indicator_timeframe_warmup",
    "extra_indicator_timeframes",
    "indicator_min_warmup",
    "indicator_offset",
    "indicator_output_series",
    "indicator_value_keys",
    "is_valid_htf_pair",
    "is_valid_reference_pair",
    "lockstep_product_ids",
    "operand_value_key",
    "pyramiding_enabled",
    "reference_data_requirements",
    "reference_indicator_groups",
    "reference_instruments",
    "reference_series",
    "resolved_indicator_timeframe",
    "reward_risk_multiple",
    "signal_exit_condition",
    "strategy_fingerprint",
    "strategy_indicator_operands",
    "strategy_indicator_value_keys",
    "timeframe_seconds",
    "unbound_indicator_timeframes",
]

_FINGERPRINT_PREFIX = "sha256:"


def _omit_absent_indicator_inputs(indicators: object) -> None:
    """Drop null inputs, timeframes, and offsets so omitted fields keep historical fingerprints."""
    if not isinstance(indicators, list):
        return
    for item in indicators:
        if not isinstance(item, dict):
            continue
        if item.get("input") is None:
            item.pop("input", None)
        if item.get("timeframe") is None:
            item.pop("timeframe", None)
        if not item.get("offset"):
            item.pop("offset", None)
        if item.get("source") is None:
            item.pop("source", None)


def _omit_empty_references(data_requirements: object) -> None:
    """Drop empty ``reference_instruments`` so reference-free documents keep their bytes."""
    if isinstance(data_requirements, dict) and not data_requirements.get("reference_instruments"):
        data_requirements.pop("reference_instruments", None)


def _omit_absent_operand_series(node: object) -> None:
    """Drop null series fields so single-output operands keep historical fingerprints."""
    if not isinstance(node, dict):
        return
    for key in ("left", "right"):
        operand = node.get(key)
        if isinstance(operand, dict):
            if operand.get("series") is None:
                operand.pop("series", None)
            if not operand.get("offset"):
                operand.pop("offset", None)
    for children_key in ("all", "any"):
        children = node.get(children_key)
        if isinstance(children, list):
            for child in children:
                _omit_absent_operand_series(child)
    if "not" in node:
        _omit_absent_operand_series(node.get("not"))


def _omit_absent_signal_exit(exits: object) -> None:
    """Drop a null ``signal_exit`` and absent operand series so older exits keep their bytes."""
    if not isinstance(exits, dict):
        return
    signal_exit = exits.get("signal_exit")
    if not isinstance(signal_exit, dict):
        exits.pop("signal_exit", None)
        return
    _omit_absent_operand_series(signal_exit.get("when"))


class RiskFractionSizing(_FrozenModel):
    """Size positions by bounded portfolio risk and quote-notional limits."""

    kind: Literal["risk_fraction"]
    risk_fraction: DecimalText
    min_quote_notional: DecimalText
    max_quote_notional: DecimalText

    @model_validator(mode="after")
    def validate_ranges(self) -> Self:
        """Require positive bounded risk and coherent notional limits."""
        risk_fraction = Decimal(self.risk_fraction)
        minimum = Decimal(self.min_quote_notional)
        maximum = Decimal(self.max_quote_notional)
        if not Decimal("0") < risk_fraction <= Decimal("0.25"):
            raise ValueError("risk_fraction must be greater than zero and at most 0.25")
        if minimum <= 0 or maximum <= 0 or minimum > maximum:
            raise ValueError("quote sizing bounds must be positive and ordered")
        return self


class PortfolioLimits(_FrozenModel):
    """Bound exposure and distinct product-position concurrency for one document."""

    max_strategy_exposure_fraction: DecimalText
    max_concurrent_positions: int = Field(ge=1, le=8)

    @model_validator(mode="after")
    def validate_exposure(self) -> Self:
        """Require strategy exposure to remain within the portfolio."""
        exposure = Decimal(self.max_strategy_exposure_fraction)
        if not Decimal("0") < exposure <= Decimal("1"):
            raise ValueError("max_strategy_exposure_fraction must be in (0, 1]")
        return self


LEGACY_LIFECYCLE_KEYS: tuple[str, ...] = ("version", "status")
"""Retired draft/publish keys accepted on input and discarded (ADR 0082)."""


class StrategyDefinition(_FrozenModel):
    """One validated canonical strategy document (schema 1.0).

    The document carries no lifecycle state: a strategy is one mutable object and
    each backtest, study, or deployment snapshots this definition by content
    fingerprint. Legacy ``version``/``status`` keys from pre-ADR-0082 exports are
    accepted and dropped so they never reach canonical bytes or the fingerprint.
    """

    schema_version: Literal["1.0"]
    strategy_id: Uuid7
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, min_length=1, max_length=500)
    created_at: datetime
    instrument: Instrument
    additional_instruments: tuple[Instrument, ...] = Field(
        default=(),
        max_length=7,
        exclude_if=lambda value: not value,
    )
    timeframe: DatasetTimeframe
    data_requirements: DataRequirements
    indicators: tuple[IndicatorDefinition, ...] = Field(min_length=1, max_length=20)
    htf_filter: HigherTimeframeFilter | None = None
    entry: EntryDefinition
    sizing: RiskFractionSizing
    portfolio_limits: PortfolioLimits
    exits: ExitDefinition
    execution: ExecutionPreferences
    metadata: StrategyMetadata

    @model_validator(mode="before")
    @classmethod
    def drop_legacy_lifecycle_keys(cls, data: object) -> object:
        """Discard retired draft/publish lifecycle keys from mapping input."""
        if isinstance(data, dict) and any(key in data for key in LEGACY_LIFECYCLE_KEYS):
            return {key: value for key, value in data.items() if key not in LEGACY_LIFECYCLE_KEYS}
        return data

    @field_validator("created_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        """Reject naive and non-UTC strategy creation timestamps."""
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("created_at must be timezone-aware UTC")
        return value.astimezone(UTC)

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        """Serialize UTC timestamps with a canonical Z suffix."""
        return value.isoformat().replace("+00:00", "Z")

    @model_validator(mode="after")
    def validate_semantics(self) -> Self:
        """Resolve indicator references and enforce warmup sufficiency."""
        _validate_covered_instruments(self)
        _validate_decision_indicators(self)
        _validate_reference_instruments(self)
        _validate_signal_exit(self)
        _validate_htf_filter(self)
        return self


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


def _validate_covered_instruments(definition: StrategyDefinition) -> None:
    """Reject duplicate products and concurrent-position caps that exceed coverage."""
    products = covered_product_ids(definition)
    if len(products) != len(set(products)):
        raise ValueError("additional_instruments must be unique and exclude instrument.product_id")
    if len(products) > MAX_STRATEGY_INSTRUMENTS:
        raise ValueError("a strategy document may cover at most 8 spot products")
    quotes = {
        instrument.quote_currency
        for instrument in (definition.instrument, *definition.additional_instruments)
    }
    if len(quotes) != 1:
        raise ValueError("all covered instruments must share one quote currency")
    if definition.portfolio_limits.max_concurrent_positions > len(products):
        raise ValueError("max_concurrent_positions cannot exceed the number of covered products")


def _validate_decision_indicators(definition: StrategyDefinition) -> None:
    """Resolve LTF indicator identity, warmup, and ATR-stop references."""
    identifiers = [indicator.id for indicator in definition.indicators]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("indicator ids must be unique")
    known = set(identifiers)
    references = _referenced_indicator_ids(definition.entry.when)
    references.add(definition.exits.initial_stop.atr_indicator)
    trailing = definition.exits.trailing_stop
    if isinstance(trailing, AtrTrailingStop):
        references.add(trailing.atr_indicator)
    unknown = references - known
    if unknown:
        raise ValueError(f"unknown indicator references: {sorted(unknown)}")
    _require_condition_series(definition.entry.when, definition.indicators)
    _require_atr_indicator(
        definition.indicators,
        definition.exits.initial_stop.atr_indicator,
        role="initial stop",
        decision_timeframe=definition.timeframe,
    )
    if isinstance(trailing, AtrTrailingStop):
        _require_atr_indicator(
            definition.indicators,
            trailing.atr_indicator,
            role="trailing stop",
            decision_timeframe=definition.timeframe,
        )
    _validate_indicator_timeframes(definition)
    decision_indicators = decision_clock_indicators(definition)
    required_fields = {
        field for indicator in decision_indicators for field in _indicator_input_fields(indicator)
    }
    if not required_fields.issubset(definition.data_requirements.required_fields):
        raise ValueError("required_fields must include every indicator input")
    required_warmup = extra_indicator_timeframe_warmup(
        decision_indicators, operands=strategy_indicator_operands(definition)
    )
    if definition.data_requirements.warmup_bars < required_warmup:
        raise ValueError("warmup_bars must cover the longest indicator period")


def _validate_reference_instruments(definition: StrategyDefinition) -> None:
    """Resolve indicator sources against declared references (ADR 0096).

    Every ``source`` must name a declared reference, every reference must be read by at
    least one indicator, share the traded instrument's quote currency, and use the
    decision timeframe or a coarser integer multiple of it (the HTF alignment rule, with
    the decision clock itself allowed). Warmup per reference is derived from its
    indicators, so it can never be under-declared.
    """
    references = definition.data_requirements.reference_instruments
    declared = {reference.id for reference in references}
    sources = {
        indicator.source for indicator in definition.indicators if indicator.source is not None
    }
    unknown = sorted(sources - declared)
    if unknown:
        raise ValueError(
            "indicator source must name a declared data_requirements.reference_instruments "
            f"id: {unknown}"
        )
    quote = definition.instrument.quote_currency
    for reference in references:
        if reference.quote_currency != quote:
            raise ValueError(
                f"reference instrument {reference.id} ({reference.product_id}) must use the "
                f"strategy quote currency {quote}"
            )
        if not is_valid_reference_pair(definition.timeframe, reference.timeframe):
            raise ValueError(
                f"reference instrument {reference.id} timeframe {reference.timeframe} must "
                f"equal the strategy timeframe {definition.timeframe} or be a coarser integer "
                "multiple of it"
            )
    unused = sorted(declared - sources)
    if unused:
        raise ValueError(
            f"every reference instrument must be read by at least one indicator source: {unused}"
        )


def _validate_signal_exit(definition: StrategyDefinition) -> None:
    """Resolve the optional exit-rule tree with the entry operand rules (ADR 0093).

    The tree may reference any decision-list indicator (including per-indicator extra
    timeframes, like ``entry.when``) but never an HTF-filter indicator: the HTF filter
    only gates entries. Multi-series operands must name a declared series.
    """
    condition = signal_exit_condition(definition.exits)
    if condition is None:
        return
    references = _referenced_indicator_ids(condition)
    known = {indicator.id for indicator in definition.indicators}
    htf_filter = definition.htf_filter
    htf_ids = set() if htf_filter is None else {indicator.id for indicator in htf_filter.indicators}
    filter_only = sorted((references - known) & htf_ids)
    if filter_only:
        raise ValueError(f"exits.signal_exit cannot reference HTF filter indicators: {filter_only}")
    unknown = sorted(references - known)
    if unknown:
        raise ValueError(f"unknown exits.signal_exit indicator references: {unknown}")
    _require_condition_series(condition, definition.indicators)


def _require_atr_indicator(
    indicators: tuple[IndicatorDefinition, ...],
    indicator_id: str,
    *,
    role: str,
    decision_timeframe: str,
) -> None:
    """Reject a stop reference that is missing, not an ATR, or not on the decision clock."""
    atr = next((item for item in indicators if item.id == indicator_id), None)
    if atr is None or atr.kind is not IndicatorKind.ATR:
        raise ValueError(f"{role} indicator must reference an ATR")
    if atr.source is not None:
        raise ValueError(f"{role} ATR must read the traded instrument, not a reference instrument")
    if resolved_indicator_timeframe(atr, decision_timeframe) != decision_timeframe:
        raise ValueError(f"{role} ATR must use the strategy decision timeframe")


def _validate_htf_filter(definition: StrategyDefinition) -> None:
    """Reject HTF clocks that are not strictly coarser, or that reuse LTF indicator ids."""
    htf_filter = definition.htf_filter
    if htf_filter is None:
        return
    if not is_valid_htf_pair(definition.timeframe, htf_filter.timeframe):
        raise ValueError(
            "htf_filter.timeframe must be strictly coarser than the strategy decision "
            "timeframe and an integer multiple of it"
        )
    overlap = {indicator.id for indicator in definition.indicators}.intersection(
        {indicator.id for indicator in htf_filter.indicators}
    )
    if overlap:
        raise ValueError(f"HTF indicator ids must not reuse decision indicators: {sorted(overlap)}")


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


def _validate_indicator_timeframes(definition: StrategyDefinition) -> None:
    """Reject extra indicator clocks that are not coarser integer multiples of LTF."""
    for indicator in definition.indicators:
        clock = resolved_indicator_timeframe(indicator, definition.timeframe)
        if clock == definition.timeframe:
            continue
        if not is_valid_htf_pair(definition.timeframe, clock):
            raise ValueError(
                "indicator timeframe must be strictly coarser than the strategy decision "
                "timeframe and an integer multiple of it"
            )
    _require_htf_coverage_for_shared_indicator_clock(definition)


def _require_htf_coverage_for_shared_indicator_clock(definition: StrategyDefinition) -> None:
    """When extra indicators share the HTF clock, the HTF dataset must cover them."""
    htf_filter = definition.htf_filter
    if htf_filter is None:
        return
    groups = dict(extra_indicator_timeframe_groups(definition))
    shared = groups.get(htf_filter.timeframe)
    if shared is None:
        return
    needed_warmup = extra_indicator_timeframe_warmup(
        shared, operands=strategy_indicator_operands(definition)
    )
    if htf_filter.data_requirements.warmup_bars < needed_warmup:
        raise ValueError("HTF warmup_bars must cover extra indicators on the HTF timeframe")
    needed_fields = extra_indicator_required_fields(shared)
    if not set(needed_fields).issubset(htf_filter.data_requirements.required_fields):
        raise ValueError("HTF required_fields must include extra indicators on the HTF timeframe")


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


def canonical_strategy_bytes(definition: StrategyDefinition) -> bytes:
    """Revalidate and serialize a strategy into deterministic canonical UTF-8 JSON."""
    validated = StrategyDefinition.model_validate(
        definition.model_dump(mode="python", by_alias=True)
    )
    payload = validated.model_dump(mode="json", by_alias=True)
    _omit_empty_references(payload.get("data_requirements"))
    if payload.get("htf_filter") is None:
        payload.pop("htf_filter", None)
    if not payload.get("additional_instruments"):
        payload.pop("additional_instruments", None)
    entry = payload.get("entry")
    if isinstance(entry, dict) and entry.get("pyramiding") is None:
        entry.pop("pyramiding", None)
    _omit_absent_indicator_inputs(payload.get("indicators"))
    entry = payload.get("entry")
    if isinstance(entry, dict):
        _omit_absent_operand_series(entry.get("when"))
    _omit_absent_signal_exit(payload.get("exits"))
    htf_filter = payload.get("htf_filter")
    if isinstance(htf_filter, dict):
        _omit_empty_references(htf_filter.get("data_requirements"))
        _omit_absent_indicator_inputs(htf_filter.get("indicators"))
        _omit_absent_operand_series(htf_filter.get("when"))
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def strategy_fingerprint(definition: StrategyDefinition) -> str:
    """Return the SHA-256 identity of the entire canonical strategy document."""
    return f"{_FINGERPRINT_PREFIX}{sha256(canonical_strategy_bytes(definition)).hexdigest()}"
