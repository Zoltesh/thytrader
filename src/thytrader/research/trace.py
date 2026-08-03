"""Canonical immutable records emitted by deterministic signal evaluation."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from itertools import pairwise
import json
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from thytrader.research.models import (  # noqa: TC001 - Pydantic resolves aliases at runtime.
    FingerprintText,
    UtcDateTime,
)


def _validate_indicator_decimal_text(value: str) -> str:
    """Require canonical output inside the evaluator's 64-digit Decimal envelope."""
    parsed = Decimal(value)
    exponent = parsed.as_tuple().exponent
    if (
        not isinstance(exponent, int)
        or len(parsed.as_tuple().digits) > 64
        or exponent < -6206
        or parsed.adjusted() > 6144
    ):
        raise ValueError("indicator values must fit the deterministic Decimal envelope")
    return value


IndicatorId = Annotated[str, Field(strict=True, pattern=r"^[a-z][a-z0-9_]{0,63}$")]
IndicatorDecimalText = Annotated[
    str,
    Field(strict=True, pattern=r"^(?:0|[1-9]\d*)(?:\.\d*[1-9])?$", max_length=6210),
    AfterValidator(_validate_indicator_decimal_text),
]


class EntryConditionOutcome(StrEnum):
    """Auditable outcome of one completed-candle entry-condition evaluation."""

    MATCHED = "matched"
    NOT_MATCHED = "not_matched"
    UNDEFINED = "undefined"


class _FrozenTraceModel(BaseModel):
    """Reject unknown trace fields and prevent mutation after evaluation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class IndicatorTraceValue(_FrozenTraceModel):
    """One named canonical indicator value or an explicit undefined state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    indicator_id: IndicatorId
    value: IndicatorDecimalText | None


class SignalTraceRecord(_FrozenTraceModel):
    """One auditable condition result for a completed evaluation candle."""

    candle_starts_at: UtcDateTime
    indicator_values: tuple[IndicatorTraceValue, ...] = Field(min_length=1)
    entry_condition: EntryConditionOutcome

    @field_validator("indicator_values")
    @classmethod
    def validate_unique_indicator_ids(
        cls,
        value: tuple[IndicatorTraceValue, ...],
    ) -> tuple[IndicatorTraceValue, ...]:
        """Require one unambiguous evidence entry per represented indicator."""
        indicator_ids = tuple(item.indicator_id for item in value)
        if len(set(indicator_ids)) != len(indicator_ids):
            raise ValueError("signal trace indicator IDs must be unique")
        return value

    @field_validator("candle_starts_at")
    @classmethod
    def validate_candle_start_utc(cls, value: datetime) -> datetime:
        """Require a timezone-aware zero-offset timestamp for canonical trace identity."""
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("candle_starts_at must be a timezone-aware UTC datetime")
        return value

    @field_serializer("candle_starts_at", when_used="json")
    def serialize_candle_start(self, value: datetime) -> str:
        """Serialize the UTC candle boundary with a canonical Z suffix."""
        return value.isoformat().replace("+00:00", "Z")


class SignalTrace(_FrozenTraceModel):
    """Deterministic entry-condition evidence for one immutable research request."""

    schema_version: Literal["1.0"]
    run_fingerprint: FingerprintText
    strategy_fingerprint: FingerprintText
    dataset_fingerprint: FingerprintText
    engine_contract_version: Literal[
        "thytrader-bar-signal-v1",
        "thytrader-bar-backtest-v1",
        "thytrader-bar-backtest-v2",
    ]
    indicator_ids: tuple[IndicatorId, ...] = Field(min_length=1)
    records: tuple[SignalTraceRecord, ...] = Field(min_length=1)

    @field_validator("records")
    @classmethod
    def validate_record_order(
        cls,
        value: tuple[SignalTraceRecord, ...],
    ) -> tuple[SignalTraceRecord, ...]:
        """Require unique records in strictly increasing candle order."""
        if any(
            current.candle_starts_at <= previous.candle_starts_at
            for previous, current in pairwise(value)
        ):
            raise ValueError("signal trace records must be strictly increasing")
        return value

    @model_validator(mode="after")
    def validate_indicator_sequence(self) -> Self:
        """Bind every record to the exact identity-bearing indicator declaration order."""
        if len(set(self.indicator_ids)) != len(self.indicator_ids):
            raise ValueError("signal trace indicator sequence must contain unique IDs")
        if any(
            tuple(item.indicator_id for item in record.indicator_values) != self.indicator_ids
            for record in self.records
        ):
            raise ValueError("signal trace records must match the exact indicator sequence")
        return self


def canonical_signal_trace_bytes(trace: SignalTrace) -> bytes:
    """Revalidate and serialize one trace into sorted compact canonical JSON bytes."""
    validated = SignalTrace.model_validate(trace.model_dump(mode="python"))
    return json.dumps(
        validated.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def signal_trace_fingerprint(trace: SignalTrace) -> str:
    """Return the SHA-256 identity of a complete canonical signal trace."""
    return f"sha256:{sha256(canonical_signal_trace_bytes(trace)).hexdigest()}"
