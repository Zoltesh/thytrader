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

_FINGERPRINT_PREFIX = "sha256:"
_FINGERPRINT_PATTERN = r"^sha256:[0-9a-f]{64}$"
_DECIMAL_TEXT_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_MAX_DECIMAL_TEXT_LENGTH = 64
_MAX_INITIAL_QUOTE_BALANCE = Decimal("1000000000000000000")
_MAX_FEE_RATE = Decimal("0.1")
_MAX_SLIPPAGE_BPS = Decimal("1000")


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


def _require_utc_hour(value: datetime, *, label: str) -> datetime:
    """Require one exact whole-hour UTC candle boundary."""
    normalized = _require_utc(value, label=label)
    if normalized.minute or normalized.second or normalized.microsecond:
        raise ValueError(f"{label} must be a whole-hour UTC boundary")
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
        """Require every evaluation boundary to be an exact UTC hour."""
        field_name = getattr(info, "field_name", "evaluation timestamp")
        return _require_utc_hour(value, label=str(field_name))

    @field_serializer("starts_at", "ends_at", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        """Serialize evaluation boundaries canonically."""
        return _utc_text(value)

    @model_validator(mode="after")
    def require_nonempty_interval(self) -> Self:
        """Require at least one completed hourly candle in the evaluation interval."""
        if self.ends_at - self.starts_at < timedelta(hours=1):
            raise ValueError("evaluation interval must contain at least one hourly candle")
        return self


class WarmupWindow(_FrozenModel):
    """Strategy-derived completed candles immediately preceding evaluation."""

    bars: int = Field(strict=True, ge=1, le=10_000)
    starts_at: UtcDateTime

    @field_validator("starts_at")
    @classmethod
    def require_hour_boundary(cls, value: datetime) -> datetime:
        """Require the warmup boundary to be an exact UTC hour."""
        return _require_utc_hour(value, label="warmup starts_at")

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
    """Fixed no-lookahead timing convention for the first bar-level engine contract."""

    signal_timing: Literal["completed_candle_close"]
    fill_timing: Literal["next_candle_open"]


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
    bar_execution: BarExecutionAssumptions
    engine_contract_version: Literal["thytrader-bar-v1", "thytrader-bar-signal-v1"]
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
        """Require warmup to end at evaluation start with exactly the declared hourly bars."""
        expected_start = self.evaluation.starts_at - timedelta(hours=self.warmup.bars)
        if self.warmup.starts_at != expected_start:
            raise ValueError(
                "warmup starts_at must equal evaluation starts_at minus the declared warmup bars"
            )
        return self


def canonical_research_run_bytes(specification: ResearchRunSpecification) -> bytes:
    """Revalidate and serialize a run specification into deterministic canonical UTF-8 JSON."""
    validated = ResearchRunSpecification.model_validate(specification.model_dump(mode="python"))
    payload = validated.model_dump(mode="json")
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
