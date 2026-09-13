"""Canonical immutable specifications for reproducible research runs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from thytrader.market_data.models import CandleInterval, parse_candle_interval

_FINGERPRINT_PREFIX = "sha256:"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_DECIMAL_TEXT_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_MAX_DECIMAL_TEXT_LENGTH = 64
_MAX_INITIAL_QUOTE_BALANCE = Decimal("1000000000000000000")
_MAX_FEE_RATE = Decimal("0.1")
_MAX_SLIPPAGE_BPS = Decimal("1000")
_MAX_SPREAD_BPS = Decimal("1000")


def _decimal_text(value: str) -> str:
    """Validate and lexically normalize one non-negative finite plain decimal string."""
    if len(value) > _MAX_DECIMAL_TEXT_LENGTH or not _DECIMAL_TEXT_PATTERN.fullmatch(value):
        raise ValueError("financial assumptions must be non-negative plain decimal strings")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("financial assumptions must be valid decimal strings") from error
    if not parsed.is_finite():
        raise ValueError("financial assumptions must be finite")
    whole, separator, fraction = value.partition(".")
    canonical_whole = whole.lstrip("0") or "0"
    canonical_fraction = fraction.rstrip("0") if separator else ""
    decimal_places = f".{canonical_fraction}" if canonical_fraction else ""
    return f"{canonical_whole}{decimal_places}"


DecimalText = Annotated[str, Field(strict=True), AfterValidator(_decimal_text)]
UtcDateTime = Annotated[datetime, Field(strict=True)]
FingerprintText = Annotated[str, Field(strict=True, pattern=_FINGERPRINT_PATTERN)]
StrictUuid = Annotated[UUID, Field(strict=True)]


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _require_utc(value: datetime, *, label: str) -> datetime:
    """Require one timezone-aware UTC timestamp and normalize its timezone object."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must be timezone-aware UTC")
    return value.astimezone(UTC)


def _require_utc_candle_boundary(value: datetime, *, label: str) -> datetime:
    """Require a timezone-aware UTC instant aligned to a 1h or 5m candle start."""
    normalized = _require_utc(value, label=label)
    if normalized.second or normalized.microsecond or normalized.minute % 5:
        raise ValueError(f"{label} must be an aligned UTC candle boundary")
    return normalized


def _utc_text(value: datetime) -> str:
    """Serialize a UTC timestamp using the canonical RFC 3339 Z suffix."""
    return value.isoformat().replace("+00:00", "Z")


class EvaluationWindow(_FrozenModel):
    """Half-open completed-candle interval whose signals will be evaluated."""

    starts_at: UtcDateTime
    ends_at: UtcDateTime

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_hour_boundary(cls, value: datetime, info: object) -> datetime:
        """Require every evaluation boundary to align to a 1h or 5m candle start."""
        field_name = getattr(info, "field_name", "evaluation timestamp")
        return _require_utc_candle_boundary(value, label=str(field_name))

    @field_serializer("starts_at", "ends_at", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        """Serialize evaluation boundaries canonically."""
        return _utc_text(value)

    @model_validator(mode="after")
    def require_nonempty_interval(self) -> Self:
        """Require at least one completed 5m candle in the evaluation interval."""
        if self.ends_at - self.starts_at < CandleInterval.FIVE_MINUTES.duration:
            raise ValueError("evaluation interval must contain at least one candle")
        return self


class WarmupWindow(_FrozenModel):
    """Strategy-derived completed candles immediately preceding evaluation."""

    bars: int = Field(strict=True, ge=1, le=10_000)
    starts_at: UtcDateTime

    @field_validator("starts_at")
    @classmethod
    def require_hour_boundary(cls, value: datetime) -> datetime:
        """Require the warmup boundary to align to a 1h or 5m candle start."""
        return _require_utc_candle_boundary(value, label="warmup starts_at")

    @field_serializer("starts_at", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        """Serialize the warmup boundary canonically."""
        return _utc_text(value)


class CapitalAssumptions(_FrozenModel):
    """Exact starting quote capital for one USD spot simulation."""

    quote_currency: Literal["USD"]
    initial_quote_balance: DecimalText

    @field_validator("initial_quote_balance")
    @classmethod
    def require_positive_bounded_balance(cls, value: str) -> str:
        """Require positive capital within the supported research bound."""
        amount = Decimal(value)
        if amount <= 0 or amount > _MAX_INITIAL_QUOTE_BALANCE:
            raise ValueError("initial_quote_balance must be greater than 0 and at most 1e18")
        return value


class CostAssumptions(_FrozenModel):
    """Exact deterministic fee and fixed-slippage assumptions."""

    maker_fee_rate: DecimalText
    taker_fee_rate: DecimalText
    fixed_slippage_bps: DecimalText

    @field_validator("maker_fee_rate", "taker_fee_rate")
    @classmethod
    def require_bounded_fee(cls, value: str) -> str:
        """Bound each fee rate to the explicit zero-through-ten-percent range."""
        if Decimal(value) > _MAX_FEE_RATE:
            raise ValueError("fee rates must be at most 0.1")
        return value

    @field_validator("fixed_slippage_bps")
    @classmethod
    def require_bounded_slippage(cls, value: str) -> str:
        """Bound fixed slippage to a deliberately conservative maximum."""
        if Decimal(value) > _MAX_SLIPPAGE_BPS:
            raise ValueError("fixed_slippage_bps must be at most 1000")
        return value

    @model_validator(mode="after")
    def require_maker_not_above_taker(self) -> Self:
        """Require the maker fee assumption not to exceed the taker fee assumption."""
        if Decimal(self.maker_fee_rate) > Decimal(self.taker_fee_rate):
            raise ValueError("maker_fee_rate must not exceed taker_fee_rate")
        return self


class BarExecutionAssumptions(_FrozenModel):
    """Fixed no-lookahead timing. V1/V2 use next-open fills; V3 rests a close-limit."""

    signal_timing: Literal["completed_candle_close"]
    fill_timing: Literal["next_candle_open", "resting_maker_limit"]
    limit_at: Literal["completed_close"] | None = None


class BrokerAssumptions(_FrozenModel):
    """Fully disclosed deterministic broker assumptions, independent of ambient configuration."""

    price_model: Literal["constant_spread_bps", "post_only_limit"]
    spread_bps: DecimalText
    fill_policy: Literal["full", "resting_limit"]
    trigger_evaluation: Literal["bid_side", "bar_extreme"]
    equity_marking: Literal["bid_close", "last_close"]

    @field_validator("spread_bps")
    @classmethod
    def require_bounded_spread(cls, value: str) -> str:
        """Bound fixed quoted spread to a deliberately conservative maximum."""
        if Decimal(value) > _MAX_SPREAD_BPS:
            raise ValueError("spread_bps must be at most 1000")
        return value


class ResearchRunSpecification(_FrozenModel):
    """Immutable identity-bearing request for a future deterministic research simulation."""

    schema_version: Literal["1.0"]
    run_id: StrictUuid
    created_at: UtcDateTime
    strategy_fingerprint: FingerprintText
    dataset_fingerprint: FingerprintText
    evaluation: EvaluationWindow
    warmup: WarmupWindow
    capital: CapitalAssumptions
    costs: CostAssumptions
    broker: BrokerAssumptions | None = None
    bar_execution: BarExecutionAssumptions
    engine_contract_version: Literal[
        "thytrader-bar-v1",
        "thytrader-bar-signal-v1",
        "thytrader-bar-backtest-v1",
        "thytrader-bar-backtest-v2",
        "thytrader-bar-backtest-v3",
    ]
    random_seed: int = Field(strict=True, ge=0, le=2**63 - 1)

    @field_validator("run_id")
    @classmethod
    def require_uuid7(cls, value: UUID) -> UUID:
        """Use time-sortable UUIDv7 identifiers for run-request identity."""
        if value.version != 7:
            raise ValueError("run_id must be UUIDv7")
        return value

    @field_validator("created_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime) -> datetime:
        """Require a timezone-aware UTC creation instant."""
        return _require_utc(value, label="created_at")

    @field_serializer("created_at", when_used="json")
    def serialize_created_at(self, value: datetime) -> str:
        """Serialize the creation instant canonically."""
        return _utc_text(value)

    @model_validator(mode="after")
    def require_run_id_creation_millisecond(self) -> Self:
        """Bind the UUIDv7 timestamp to the canonical request creation millisecond."""
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        elapsed = self.created_at - epoch
        created_at_milliseconds = (
            elapsed.days * 86_400_000 + elapsed.seconds * 1_000 + elapsed.microseconds // 1_000
        )
        if self.run_id.time != created_at_milliseconds:
            raise ValueError("run_id timestamp must match the created_at UTC millisecond")
        return self

    @model_validator(mode="after")
    def require_derived_warmup_range(self) -> Self:
        """Require warmup to end at evaluation start with 1h or 5m bar spacing."""
        try:
            specification_bar_interval(self)
        except OverflowError as error:
            raise ValueError("warmup range cannot represent the declared bars") from error
        except ValueError as error:
            raise ValueError(str(error)) from error
        return self

    @model_validator(mode="after")
    def require_broker_for_spread_and_maker_contracts(self) -> Self:
        """Bind broker and fill-timing literals to the engine contract that owns them."""
        if self.engine_contract_version == "thytrader-bar-backtest-v3":
            _require_v3_maker_assumptions(self)
            return self
        if (
            self.bar_execution.fill_timing != "next_candle_open"
            or self.bar_execution.limit_at is not None
        ):
            raise ValueError("resting maker fills require the backtest V3 contract")
        _require_v2_broker_exclusivity(self)
        return self


def _require_v3_maker_assumptions(specification: ResearchRunSpecification) -> None:
    """V3 identity includes resting close-limit fills and the post-only broker block."""
    if (
        specification.bar_execution.fill_timing != "resting_maker_limit"
        or specification.bar_execution.limit_at != "completed_close"
    ):
        raise ValueError("backtest V3 requires resting_maker_limit at completed_close")
    broker = specification.broker
    if broker is None:
        raise ValueError("backtest V3 requires broker assumptions")
    if (
        broker.price_model != "post_only_limit"
        or broker.fill_policy != "resting_limit"
        or broker.trigger_evaluation != "bar_extreme"
        or broker.equity_marking != "last_close"
    ):
        raise ValueError("backtest V3 requires post-only resting-limit broker assumptions")


def _require_v2_broker_exclusivity(specification: ResearchRunSpecification) -> None:
    """V2 is the only non-v3 contract that may carry a constant-spread broker block."""
    is_v2 = specification.engine_contract_version == "thytrader-bar-backtest-v2"
    if is_v2 and specification.broker is None:
        raise ValueError("backtest V2 requires broker assumptions")
    if not is_v2 and specification.broker is not None:
        raise ValueError("broker assumptions require the backtest V2 contract")
    broker = specification.broker
    if broker is not None and (
        broker.price_model != "constant_spread_bps"
        or broker.fill_policy != "full"
        or broker.trigger_evaluation != "bid_side"
        or broker.equity_marking != "bid_close"
    ):
        raise ValueError("backtest V2 requires full-fill constant-spread broker assumptions")


def specification_bar_interval(specification: ResearchRunSpecification) -> CandleInterval:
    """Infer 1h or 5m from warmup spacing. Does not invent unsupported intervals."""
    span = specification.evaluation.starts_at - specification.warmup.starts_at
    for interval in CandleInterval:
        if not interval.execution_supported:
            continue
        if span == interval.duration * specification.warmup.bars:
            return interval
    raise ValueError(
        "warmup starts_at must equal evaluation starts_at minus the declared warmup bars"
    )


def warmup_starts_at(evaluation_starts_at: datetime, bars: int, timeframe: str) -> datetime:
    """Derive the warmup window start from one strategy timeframe."""
    interval = parse_candle_interval(timeframe)
    if not interval.execution_supported:
        raise ValueError("warmup windows require a 1h or 5m strategy timeframe.")
    return evaluation_starts_at - interval.duration * bars


def canonical_research_run_bytes(specification: ResearchRunSpecification) -> bytes:
    """Revalidate and serialize a run specification into deterministic canonical UTF-8 JSON."""
    validated = ResearchRunSpecification.model_validate(specification.model_dump(mode="python"))
    payload = validated.model_dump(mode="json", exclude_none=True)
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def research_run_fingerprint(specification: ResearchRunSpecification) -> str:
    """Return the SHA-256 identity of the complete canonical run specification."""
    digest = sha256(canonical_research_run_bytes(specification)).hexdigest()
    return f"{_FINGERPRINT_PREFIX}{digest}"
