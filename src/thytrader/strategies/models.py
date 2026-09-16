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

from thytrader.market_data.models import (
    DatasetTimeframe,
    EXECUTION_TIMEFRAMES,
    parse_candle_interval,
)

_FINGERPRINT_PREFIX = "sha256:"
_MAX_CONDITION_DEPTH = 4
_MAX_CONDITION_NODES = 64
STRATEGY_DECISION_TIMEFRAMES: tuple[DatasetTimeframe, ...] = EXECUTION_TIMEFRAMES
STRATEGY_HTF_TIMEFRAMES: tuple[DatasetTimeframe, ...] = EXECUTION_TIMEFRAMES


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
    """Bounded period parameters shared by the implemented rolling indicator profile."""

    period: int = Field(ge=2, le=500)


class MacdIndicatorParameters(_FrozenModel):
    """Close-locked MACD EMA windows: fast line, slow line, and signal smoothing."""

    fast_period: int = Field(ge=2, le=500)
    slow_period: int = Field(ge=2, le=500)
    signal_period: int = Field(ge=2, le=500)

    @model_validator(mode="after")
    def validate_fast_shorter_than_slow(self) -> Self:
        """Require the fast EMA window to be strictly shorter than the slow window."""
        if self.fast_period >= self.slow_period:
            raise ValueError("macd fast_period must be less than slow_period")
        return self


class BollingerIndicatorParameters(_FrozenModel):
    """Close-locked SMA period and population-stdev band multiplier."""

    period: int = Field(ge=2, le=500)
    stdev_multiplier: DecimalText

    @model_validator(mode="after")
    def validate_multiplier_bounds(self) -> Self:
        """Reject non-positive or unbounded Bollinger width multipliers."""
        parsed = Decimal(self.stdev_multiplier)
        if parsed <= 0:
            raise ValueError("bollinger stdev_multiplier must be greater than 0")
        if parsed > Decimal(10):
            raise ValueError("bollinger stdev_multiplier must be at most 10")
        return self


class ConstantIndicatorParameters(_FrozenModel):
    """Named finite level repeated on every completed bar."""

    value: DecimalText


class EmptyIndicatorParameters(_FrozenModel):
    """No rolling or level parameters; used by identity OHLCV kinds."""


IndicatorParameterBlock = (
    MacdIndicatorParameters
    | BollingerIndicatorParameters
    | IndicatorParameters
    | ConstantIndicatorParameters
    | EmptyIndicatorParameters
)


class IndicatorKind(StrEnum):
    """Fail-closed kinds in the canonical indicator registry."""

    EMA = "ema"
    SMA = "sma"
    RSI = "rsi"
    ATR = "atr"
    VOLUME_SMA = "volume_sma"
    HIGHEST = "highest"
    LOWEST = "lowest"
    STDEV = "stdev"
    ROC = "roc"
    WILLIAMS_R = "williams_r"
    CCI = "cci"
    IDENTITY = "identity"
    CONSTANT = "constant"
    WMA = "wma"
    MOMENTUM = "momentum"
    MFI = "mfi"
    MACD = "macd"
    BOLLINGER = "bollinger"


_SINGLE_SOURCE_INPUT: dict[IndicatorKind, Literal["high", "low", "close", "volume"]] = {
    IndicatorKind.EMA: "close",
    IndicatorKind.SMA: "close",
    IndicatorKind.RSI: "close",
    IndicatorKind.VOLUME_SMA: "volume",
    IndicatorKind.HIGHEST: "high",
    IndicatorKind.LOWEST: "low",
    IndicatorKind.STDEV: "close",
    IndicatorKind.ROC: "close",
    IndicatorKind.WMA: "close",
    IndicatorKind.MOMENTUM: "close",
    IndicatorKind.MACD: "close",
    IndicatorKind.BOLLINGER: "close",
}
MACD_OUTPUT_SERIES: tuple[str, ...] = ("macd", "signal", "histogram")
BOLLINGER_OUTPUT_SERIES: tuple[str, ...] = ("middle", "upper", "lower")
_INDICATOR_OUTPUT_SERIES: dict[IndicatorKind, tuple[str, ...]] = {
    IndicatorKind.MACD: MACD_OUTPUT_SERIES,
    IndicatorKind.BOLLINGER: BOLLINGER_OUTPUT_SERIES,
}
_HLC_INPUT_KINDS = frozenset({IndicatorKind.ATR, IndicatorKind.WILLIAMS_R, IndicatorKind.CCI})
_HLCV_INPUT_KINDS = frozenset({IndicatorKind.MFI})
_SHORT_PERIOD_KINDS = frozenset(
    {
        IndicatorKind.RSI,
        IndicatorKind.ATR,
        IndicatorKind.WILLIAMS_R,
        IndicatorKind.CCI,
        IndicatorKind.MFI,
    }
)
_LOOKBACK_WARMUP_KINDS = frozenset(
    {IndicatorKind.RSI, IndicatorKind.ROC, IndicatorKind.MOMENTUM, IndicatorKind.MFI}
)
_UNIT_WARMUP_KINDS = frozenset({IndicatorKind.IDENTITY, IndicatorKind.CONSTANT})
_IDENTITY_INPUTS = frozenset({"open", "high", "low", "close", "volume"})


class IndicatorDefinition(_FrozenModel):
    """One named declarative indicator with no executable expression surface."""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: IndicatorKind
    input: (
        Literal["open", "high", "low", "close", "volume"]
        | tuple[
            Literal["high"],
            Literal["low"],
            Literal["close"],
        ]
        | tuple[
            Literal["high"],
            Literal["low"],
            Literal["close"],
            Literal["volume"],
        ]
        | None
    ) = None
    parameters: IndicatorParameterBlock

    @model_validator(mode="after")
    def validate_kind_period(self) -> Self:
        """Apply conservative V1 period bounds and locked inputs by indicator kind."""
        if self.kind is IndicatorKind.IDENTITY:
            _require_identity_indicator(self)
            return self
        if self.kind is IndicatorKind.CONSTANT:
            _require_constant_indicator(self)
            return self
        if self.kind is IndicatorKind.MACD:
            _require_macd_indicator(self)
            return self
        if self.kind is IndicatorKind.BOLLINGER:
            _require_bollinger_indicator(self)
            return self
        _require_period_indicator(self)
        return self


class IndicatorOperand(_FrozenModel):
    """Reference a previously declared indicator, and a series when the kind is multi-output."""

    indicator: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    series: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]{0,31}$",
        exclude_if=lambda value: value is None,
    )


def indicator_output_series(kind: IndicatorKind) -> tuple[str, ...] | None:
    """Return declared output names for multi-series kinds, or None for a single value."""
    return _INDICATOR_OUTPUT_SERIES.get(kind)


def indicator_value_keys(indicator: IndicatorDefinition) -> tuple[str, ...]:
    """Return evaluator and trace keys for one indicator's output series."""
    series = indicator_output_series(indicator.kind)
    if series is None:
        return (indicator.id,)
    return tuple(f"{indicator.id}.{name}" for name in series)


def operand_value_key(operand: IndicatorOperand) -> str:
    """Return the evaluator key addressed by one indicator operand."""
    if operand.series is None:
        return operand.indicator
    return f"{operand.indicator}.{operand.series}"


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


def _require_macd_indicator(indicator: IndicatorDefinition) -> None:
    """Reject MACD kinds that omit the three EMA periods or unlock close."""
    if not isinstance(indicator.parameters, MacdIndicatorParameters):
        raise ValueError(  # noqa: TRY004
            "macd parameters must declare fast_period, slow_period, and signal_period"
        )
    _require_locked_source(indicator)


def _require_bollinger_indicator(indicator: IndicatorDefinition) -> None:
    """Reject Bollinger kinds that omit period and multiplier or unlock close."""
    if not isinstance(indicator.parameters, BollingerIndicatorParameters):
        raise ValueError(  # noqa: TRY004
            "bollinger parameters must declare period and stdev_multiplier"
        )
    _require_locked_source(indicator)


def _require_identity_indicator(indicator: IndicatorDefinition) -> None:
    """Reject identity kinds that carry rolling parameters or a non-OHLCV source."""
    if not isinstance(indicator.parameters, EmptyIndicatorParameters):
        raise ValueError("identity parameters must be an empty object")  # noqa: TRY004
    if indicator.input not in _IDENTITY_INPUTS:
        raise ValueError("identity input must be one of open, high, low, close, volume")


def _require_constant_indicator(indicator: IndicatorDefinition) -> None:
    """Reject constant kinds that declare an OHLCV input or a period."""
    if not isinstance(indicator.parameters, ConstantIndicatorParameters):
        raise ValueError("constant parameters must declare value")  # noqa: TRY004
    if indicator.input is not None:
        raise ValueError("constant must omit input")


def _require_period_indicator(indicator: IndicatorDefinition) -> None:
    """Reject period kinds with the wrong parameter object, bounds, or locked source."""
    if not isinstance(indicator.parameters, IndicatorParameters):
        raise ValueError(f"{indicator.kind.value} parameters must declare period")  # noqa: TRY004
    maximum = 100 if indicator.kind in _SHORT_PERIOD_KINDS else 500
    if indicator.parameters.period > maximum:
        raise ValueError(f"{indicator.kind.name} period exceeds {maximum}")
    _require_locked_source(indicator)


def _require_locked_source(indicator: IndicatorDefinition) -> None:
    """Reject period kinds whose input is not the registry-locked OHLCV source."""
    if indicator.kind in _HLC_INPUT_KINDS:
        if indicator.input != ("high", "low", "close"):
            if indicator.kind is IndicatorKind.ATR:
                raise ValueError("ATR input must be high, low, close in canonical order")
            raise ValueError(
                f"{indicator.kind.value} input must be high, low, close in canonical order"
            )
        return
    if indicator.kind in _HLCV_INPUT_KINDS:
        if indicator.input != ("high", "low", "close", "volume"):
            raise ValueError(
                f"{indicator.kind.value} input must be high, low, close, volume in canonical order"
            )
        return
    expected = _SINGLE_SOURCE_INPUT[indicator.kind]
    if indicator.input != expected:
        raise ValueError(f"{indicator.kind.value} input must be {expected}")


def _indicator_min_warmup(indicator: IndicatorDefinition) -> int:
    """Return the closed-bar count required before one indicator produces a value."""
    if indicator.kind in _UNIT_WARMUP_KINDS:
        return 1
    if indicator.kind is IndicatorKind.MACD:
        return _macd_min_warmup(indicator)
    if indicator.kind is IndicatorKind.BOLLINGER:
        return _bollinger_min_warmup(indicator)
    extra = 1 if indicator.kind in _LOOKBACK_WARMUP_KINDS else 0
    parameters = indicator.parameters
    if not isinstance(parameters, IndicatorParameters):
        raise ValueError(f"{indicator.kind.value} parameters must declare period")  # noqa: TRY004
    return parameters.period + extra


def _macd_min_warmup(indicator: IndicatorDefinition) -> int:
    """Return bars before MACD signal and histogram are defined."""
    parameters = indicator.parameters
    if not isinstance(parameters, MacdIndicatorParameters):
        raise ValueError(  # noqa: TRY004
            "macd parameters must declare fast_period, slow_period, and signal_period"
        )
    return parameters.slow_period + parameters.signal_period - 1


def _bollinger_min_warmup(indicator: IndicatorDefinition) -> int:
    """Return bars before Bollinger middle and bands are defined."""
    parameters = indicator.parameters
    if not isinstance(parameters, BollingerIndicatorParameters):
        raise ValueError(  # noqa: TRY004
            "bollinger parameters must declare period and stdev_multiplier"
        )
    return parameters.period


def _indicator_input_fields(
    indicator: IndicatorDefinition,
) -> tuple[str, ...]:
    """Return the OHLCV fields one indicator consumes."""
    source = indicator.input
    if source is None:
        return ()
    if isinstance(source, str):
        return (source,)
    return source


def _omit_absent_indicator_inputs(indicators: object) -> None:
    """Drop null inputs so constant kinds omit the field from canonical JSON."""
    if not isinstance(indicators, list):
        return
    for item in indicators:
        if isinstance(item, dict) and item.get("input") is None:
            item.pop("input", None)


def _omit_absent_operand_series(node: object) -> None:
    """Drop null series fields so single-output operands keep historical fingerprints."""
    if not isinstance(node, dict):
        return
    for key in ("left", "right"):
        operand = node.get(key)
        if isinstance(operand, dict) and operand.get("series") is None:
            operand.pop("series", None)
    for children_key in ("all", "any"):
        children = node.get(children_key)
        if isinstance(children, list):
            for child in children:
                _omit_absent_operand_series(child)
    if "not" in node:
        _omit_absent_operand_series(node.get("not"))


def _require_operand_series(operand: IndicatorOperand, indicator: IndicatorDefinition) -> None:
    """Require series on multi-output kinds and forbid it on single-output kinds."""
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


def timeframe_seconds(timeframe: str) -> int:
    """Return the exact duration of one supported strategy timeframe in seconds."""
    try:
        return int(parse_candle_interval(timeframe).duration.total_seconds())
    except ValueError as error:
        raise ValueError(f"unsupported strategy timeframe: {timeframe}") from error


def is_valid_htf_pair(decision_timeframe: str, htf_timeframe: str) -> bool:
    """Return whether HTF is strictly coarser and an integer multiple of the LTF clock."""
    try:
        decision_seconds = timeframe_seconds(decision_timeframe)
        htf_seconds = timeframe_seconds(htf_timeframe)
    except ValueError:
        return False
    if htf_seconds <= decision_seconds:
        return False
    return htf_seconds % decision_seconds == 0


class EntryDefinition(_FrozenModel):
    """Define conservative long-only entry intent and cooldown limits."""

    side: Literal["long"]
    when: ConditionGroup
    cooldown_bars: int = Field(ge=0, le=10_000)
    max_open_positions: Literal[1]

    @model_validator(mode="after")
    def validate_condition_complexity(self) -> Self:
        """Reject condition trees whose bounded grammar could exhaust consumers."""
        _require_bounded_condition_tree(self.when)
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
        _require_condition_series(self.when, self.indicators)
        required_fields = {
            field for indicator in self.indicators for field in _indicator_input_fields(indicator)
        }
        if not required_fields.issubset(self.data_requirements.required_fields):
            raise ValueError("HTF required_fields must include every HTF indicator input")
        required_warmup = max(_indicator_min_warmup(indicator) for indicator in self.indicators)
        if self.data_requirements.warmup_bars < required_warmup:
            raise ValueError("HTF warmup_bars must cover the longest HTF indicator period")
        _require_bounded_condition_tree(self.when)
        return self


class TimeframeDataRequirement(_FrozenModel):
    """One product timeframe a published strategy must bind for research."""

    timeframe: str
    warmup_bars: int
    required_fields: tuple[Literal["open", "high", "low", "close", "volume"], ...]
    role: Literal["decision", "filter"]


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
    """Explicitly disable trailing stops. Canonical JSON is only ``enabled: false``."""

    enabled: Literal[False]


class AtrTrailingStop(_FrozenModel):
    """Raise a long stop from the highest high using a named ATR multiple."""

    enabled: Literal[True]
    kind: Literal["atr_multiple"]
    atr_indicator: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    multiple: DecimalText

    @field_validator("multiple")
    @classmethod
    def require_bounded_multiple(cls, value: str) -> str:
        """Require the same ATR-multiple range as the initial stop."""
        if not Decimal("0.5") <= Decimal(value) <= Decimal("10"):
            raise ValueError("ATR trailing multiple must be between 0.5 and 10")
        return value


TrailingStopDefinition = Annotated[
    DisabledTrailingStop | AtrTrailingStop,
    Field(discriminator="enabled"),
]


class TimeExit(_FrozenModel):
    """Close intent after a bounded number of completed holding bars."""

    max_bars_held: int = Field(ge=1, le=100_000)


class ExitDefinition(_FrozenModel):
    """Declare initial-stop, take-profit, optional ATR trailing, and time-exit policy."""

    initial_stop: AtrMultipleStop
    take_profit: RewardRiskTakeProfit
    trailing_stop: TrailingStopDefinition
    time_exit: TimeExit


def atr_trailing_stop(exits: ExitDefinition) -> AtrTrailingStop | None:
    """Return the enabled ATR trailing policy, or None when trailing is disabled."""
    stop = exits.trailing_stop
    if isinstance(stop, AtrTrailingStop):
        return stop
    return None


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
        _validate_decision_indicators(self)
        _validate_htf_filter(self)
        return self


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
    )
    if isinstance(trailing, AtrTrailingStop):
        _require_atr_indicator(
            definition.indicators,
            trailing.atr_indicator,
            role="trailing stop",
        )
    required_fields = {
        field for indicator in definition.indicators for field in _indicator_input_fields(indicator)
    }
    if not required_fields.issubset(definition.data_requirements.required_fields):
        raise ValueError("required_fields must include every indicator input")
    required_warmup = max(_indicator_min_warmup(indicator) for indicator in definition.indicators)
    if definition.data_requirements.warmup_bars < required_warmup:
        raise ValueError("warmup_bars must cover the longest indicator period")


def _require_atr_indicator(
    indicators: tuple[IndicatorDefinition, ...],
    indicator_id: str,
    *,
    role: str,
) -> None:
    """Reject a stop reference that is missing or not an ATR."""
    atr = next((item for item in indicators if item.id == indicator_id), None)
    if atr is None or atr.kind is not IndicatorKind.ATR:
        raise ValueError(f"{role} indicator must reference an ATR")


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


def expanded_data_requirements(
    definition: StrategyDefinition,
) -> tuple[TimeframeDataRequirement, ...]:
    """Return every timeframe a research run must fingerprint and bind."""
    decision = TimeframeDataRequirement(
        timeframe=definition.timeframe,
        warmup_bars=definition.data_requirements.warmup_bars,
        required_fields=definition.data_requirements.required_fields,
        role="decision",
    )
    htf_filter = definition.htf_filter
    if htf_filter is None:
        return (decision,)
    return (
        decision,
        TimeframeDataRequirement(
            timeframe=htf_filter.timeframe,
            warmup_bars=htf_filter.data_requirements.warmup_bars,
            required_fields=htf_filter.data_requirements.required_fields,
            role="filter",
        ),
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
    if payload.get("htf_filter") is None:
        payload.pop("htf_filter", None)
    _omit_absent_indicator_inputs(payload.get("indicators"))
    entry = payload.get("entry")
    if isinstance(entry, dict):
        _omit_absent_operand_series(entry.get("when"))
    htf_filter = payload.get("htf_filter")
    if isinstance(htf_filter, dict):
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
