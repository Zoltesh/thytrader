"""Canonical declarative strategy definitions and immutable identity helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Annotated, Literal, Self, TypeAlias
from uuid import UUID  # noqa: TC003 - Pydantic resolves this annotation at runtime.

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

_FINGERPRINT_PREFIX = "sha256:"
_MAX_CONDITION_DEPTH = 4
_MAX_CONDITION_NODES = 64


def _decimal_text(value: str) -> str:
    """Validate and normalize one finite plain decimal string without numeric coercion."""
    if len(value) > 64 or not _DECIMAL_TEXT_PATTERN.fullmatch(value):
        raise ValueError("financial values must be plain decimal strings")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("financial values must be valid decimal strings") from error
    if not parsed.is_finite():
        raise ValueError("financial values must be finite")
    unsigned = value.removeprefix("-")
    whole, separator, fraction = unsigned.partition(".")
    canonical_whole = whole.lstrip("0") or "0"
    canonical_fraction = fraction.rstrip("0") if separator else ""
    if canonical_whole == "0" and not canonical_fraction:
        return "0"
    sign = "-" if value.startswith("-") else ""
    decimal_places = f".{canonical_fraction}" if canonical_fraction else ""
    return f"{sign}{canonical_whole}{decimal_places}"


_DECIMAL_TEXT_PATTERN = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")

DecimalText = Annotated[str, Field(strict=True), AfterValidator(_decimal_text)]


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class StrategyStatus(StrEnum):
    """Lifecycle states defined by the canonical strategy contract."""

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class Instrument(_FrozenModel):
    """One conservative Coinbase USD spot instrument."""

    product_id: str = Field(pattern=r"^[A-Z0-9]{2,20}-USD$")
    base_currency: str = Field(pattern=r"^[A-Z0-9]{2,20}$")
    quote_currency: Literal["USD"]

    @model_validator(mode="after")
    def validate_product_components(self) -> Self:
        """Require the product identifier to match its explicit currencies."""
        if self.product_id != f"{self.base_currency}-{self.quote_currency}":
            raise ValueError("product_id must match base_currency and quote_currency")
        return self


class DataRequirements(_FrozenModel):
    """Historical inputs required before strategy evaluation can begin."""

    warmup_bars: int = Field(ge=1, le=10_000)
    required_fields: tuple[Literal["open", "high", "low", "close", "volume"], ...] = Field(
        min_length=1,
        max_length=5,
    )

    @field_validator("required_fields")
    @classmethod
    def require_unique_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject duplicate candle-field declarations."""
        if len(value) != len(set(value)):
            raise ValueError("required_fields must be unique")
        return value


class IndicatorParameters(_FrozenModel):
    """Bounded period parameters shared by the implemented indicator profile."""

    period: int = Field(ge=2, le=500)


class IndicatorKind(StrEnum):
    """Indicators supported by the first canonical reference profile."""

    EMA = "ema"
    SMA = "sma"
    RSI = "rsi"
    ATR = "atr"
    VOLUME_SMA = "volume_sma"


class IndicatorDefinition(_FrozenModel):
    """One named declarative indicator with no executable expression surface."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: IndicatorKind
    input: (
        Literal["close", "volume"]
        | tuple[
            Literal["high"],
            Literal["low"],
            Literal["close"],
        ]
    )
    parameters: IndicatorParameters

    @model_validator(mode="after")
    def validate_kind_period(self) -> Self:
        """Apply conservative V1 period bounds by indicator kind."""
        maximum = 100 if self.kind in {IndicatorKind.RSI, IndicatorKind.ATR} else 500
        if self.parameters.period > maximum:
            raise ValueError(f"{self.kind.name} period exceeds {maximum}")
        if self.kind is IndicatorKind.ATR:
            if self.input != ("high", "low", "close"):
                raise ValueError("ATR input must be high, low, close in canonical order")
        elif self.kind is IndicatorKind.VOLUME_SMA:
            if self.input != "volume":
                raise ValueError("volume_sma input must be volume")
        elif self.input != "close":
            raise ValueError(f"{self.kind.value} input must be close")
        return self


class IndicatorOperand(_FrozenModel):
    """Reference a previously declared indicator by stable identifier."""

    indicator: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")


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


class EntryDefinition(_FrozenModel):
    """Define conservative long-only entry intent and cooldown limits."""

    side: Literal["long"]
    when: ConditionGroup
    cooldown_bars: int = Field(ge=0, le=10_000)
    max_open_positions: Literal[1]

    @model_validator(mode="after")
    def validate_condition_complexity(self) -> Self:
        """Reject condition trees whose bounded grammar could exhaust consumers."""
        node_count, depth = _condition_tree_size(self.when)
        if depth > _MAX_CONDITION_DEPTH:
            raise ValueError(f"condition tree depth exceeds {_MAX_CONDITION_DEPTH}")
        if node_count > _MAX_CONDITION_NODES:
            raise ValueError(f"condition tree node count exceeds {_MAX_CONDITION_NODES}")
        return self


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
    """Bound exposure and enforce the V1 single-position invariant."""

    max_strategy_exposure_fraction: DecimalText
    max_concurrent_positions: Literal[1]

    @model_validator(mode="after")
    def validate_exposure(self) -> Self:
        """Require strategy exposure to remain within the portfolio."""
        exposure = Decimal(self.max_strategy_exposure_fraction)
        if not Decimal("0") < exposure <= Decimal("1"):
            raise ValueError("max_strategy_exposure_fraction must be in (0, 1]")
        return self


class AtrMultipleStop(_FrozenModel):
    """Define an initial stop as a positive multiple of a named ATR."""

    kind: Literal["atr_multiple"]
    atr_indicator: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    multiple: DecimalText

    @field_validator("multiple")
    @classmethod
    def require_bounded_multiple(cls, value: str) -> str:
        """Require the documented ATR stop-distance range."""
        if not Decimal("0.5") <= Decimal(value) <= Decimal("10"):
            raise ValueError("ATR multiple must be between 0.5 and 10")
        return value


class RewardRiskTakeProfit(_FrozenModel):
    """Define take profit as a positive reward-to-risk ratio."""

    kind: Literal["reward_risk"]
    multiple: DecimalText

    @field_validator("multiple")
    @classmethod
    def require_bounded_multiple(cls, value: str) -> str:
        """Require the documented reward-to-risk target range."""
        if not Decimal("0.5") <= Decimal(value) <= Decimal("10"):
            raise ValueError("reward-risk multiple must be between 0.5 and 10")
        return value


class DisabledTrailingStop(_FrozenModel):
    """Explicitly disable trailing stops in the first implementation profile."""

    enabled: Literal[False]


class TimeExit(_FrozenModel):
    """Close intent after a bounded number of completed holding bars."""

    max_bars_held: int = Field(ge=1, le=100_000)


class ExitDefinition(_FrozenModel):
    """Declare initial-stop and take-profit policy without execution authority."""

    initial_stop: AtrMultipleStop
    take_profit: RewardRiskTakeProfit
    trailing_stop: DisabledTrailingStop
    time_exit: TimeExit


class ExecutionPreferences(_FrozenModel):
    """Declare venue-neutral execution preferences for later runtimes."""

    entry_preference: Literal["maker_only", "marketable_limit"]
    max_entry_wait_bars: int = Field(ge=1, le=50)
    on_unfilled_entry: Literal["cancel", "reprice"]


class StrategyMetadata(_FrozenModel):
    """Bounded human annotations that are included in immutable identity."""

    tags: tuple[str, ...] = Field(default=(), max_length=20)
    notes: tuple[str, ...] = Field(default=(), max_length=20)

    @field_validator("tags", "notes")
    @classmethod
    def validate_annotations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Require unique, non-empty bounded annotation text."""
        if len(value) != len(set(value)):
            raise ValueError("metadata values must be unique")
        if any(not item.strip() or len(item) > 500 for item in value):
            raise ValueError("metadata values must contain 1 to 500 visible characters")
        return value


class StrategyDefinition(_FrozenModel):
    """Implemented immutable subset of ThyTrader's proposed canonical V1 contract."""

    schema_version: Literal["1.0"]
    strategy_id: UUID
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, min_length=1, max_length=500)
    status: StrategyStatus
    created_at: datetime
    instrument: Instrument
    timeframe: Literal["1h"]
    data_requirements: DataRequirements
    indicators: tuple[IndicatorDefinition, ...] = Field(min_length=1, max_length=20)
    entry: EntryDefinition
    sizing: RiskFractionSizing
    portfolio_limits: PortfolioLimits
    exits: ExitDefinition
    execution: ExecutionPreferences
    metadata: StrategyMetadata

    @field_validator("strategy_id")
    @classmethod
    def require_uuid7(cls, value: UUID) -> UUID:
        """Use time-sortable UUIDv7 identifiers for strategy identity."""
        if value.version != 7:
            raise ValueError("strategy_id must be UUIDv7")
        return value

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
        identifiers = [indicator.id for indicator in self.indicators]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("indicator ids must be unique")
        known = set(identifiers)
        references = {
            operand.indicator
            for condition in _comparison_conditions(self.entry.when)
            for operand in (condition.left, condition.right)
            if isinstance(operand, IndicatorOperand)
        }
        references.add(self.exits.initial_stop.atr_indicator)
        unknown = references - known
        if unknown:
            raise ValueError(f"unknown indicator references: {sorted(unknown)}")
        atr = next(
            (
                indicator
                for indicator in self.indicators
                if indicator.id == self.exits.initial_stop.atr_indicator
            ),
            None,
        )
        if atr is None or atr.kind is not IndicatorKind.ATR:
            raise ValueError("initial stop indicator must reference an ATR")
        required_fields = {
            field
            for indicator in self.indicators
            for field in (
                (indicator.input,) if isinstance(indicator.input, str) else indicator.input
            )
        }
        if not required_fields.issubset(self.data_requirements.required_fields):
            raise ValueError("required_fields must include every indicator input")
        required_warmup = max(
            indicator.parameters.period + (1 if indicator.kind is IndicatorKind.RSI else 0)
            for indicator in self.indicators
        )
        if self.data_requirements.warmup_bars < required_warmup:
            raise ValueError("warmup_bars must cover the longest indicator period")
        return self


def canonical_strategy_bytes(definition: StrategyDefinition) -> bytes:
    """Revalidate and serialize a strategy into deterministic canonical UTF-8 JSON."""
    validated = StrategyDefinition.model_validate(
        definition.model_dump(mode="python", by_alias=True)
    )
    payload = validated.model_dump(mode="json", by_alias=True)
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
