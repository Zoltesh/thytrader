"""Canonical declarative strategy definitions and immutable identity helpers.

The schema is split by document section in ``thytrader.strategies.schema``
(``primitives``, ``market``, ``indicator_parameters``, ``indicator_definition``,
``indicator_warmup``, ``conditions``, ``timeframes``, ``entry``, ``exits``,
``execution``), with whole-document checks in ``document_validation`` and derived views
(coverage, data requirements, operands) in ``document_queries``. This module defines the
top-level ``StrategyDefinition`` with its sizing and portfolio limits and the canonical
bytes and fingerprint, and re-exports every public name of the schema modules
(``__all__``), so it stays the import path. ``PortfolioLimits`` is defined here
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

from thytrader.market_data.models import DatasetTimeframe
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
    condition_indicator_operands,
    operand_value_key,
)
from thytrader.strategies.schema.derivatives import Derivatives
from thytrader.strategies.schema.document_queries import (
    MAX_STRATEGY_INSTRUMENTS,
    can_pyramid_add,
    covered_instruments,
    covered_product_ids,
    decision_and_filter_indicators,
    decision_clock_indicators,
    expanded_data_requirements,
    extra_indicator_timeframe_groups,
    extra_indicator_timeframes,
    lockstep_product_ids,
    pyramiding_enabled,
    reference_data_requirements,
    reference_indicator_groups,
    reference_instruments,
    reference_series,
    strategy_indicator_operands,
    strategy_indicator_value_keys,
    unbound_indicator_timeframes,
)
from thytrader.strategies.schema.document_validation import (
    _validate_covered_instruments,
    _validate_decision_indicators,
    _validate_derivatives,
    _validate_htf_filter,
    _validate_reference_instruments,
    _validate_signal_exit,
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
    "Derivatives",
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
    # Futures only (ADR 0128); omitted for spot so canonical bytes do not change.
    derivatives: Derivatives | None = Field(default=None, exclude_if=lambda value: value is None)

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
        _validate_derivatives(self)
        _validate_decision_indicators(self)
        _validate_reference_instruments(self)
        _validate_signal_exit(self)
        _validate_htf_filter(self)
        return self


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
