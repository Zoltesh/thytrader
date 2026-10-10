"""Qualified UTC opening evidence, separate from legacy observed-equity stamps."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MidnightMark(BaseModel):
    """An actual closed candle price at midnight for one product; never a current mark."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    product_id: str
    closes_at: datetime
    price: Decimal = Field(gt=0, allow_inf_nan=False)

    @field_validator("price", mode="before")
    @classmethod
    def reject_float(cls, value: object) -> object:
        """Reject lossy external numeric evidence before Decimal parsing."""
        if isinstance(value, (float, bool)):
            raise TypeError("Midnight prices must be exact decimals, not floats or booleans.")
        return value

    @field_validator("closes_at")
    @classmethod
    def require_midnight(cls, value: datetime) -> datetime:
        """Only an aware UTC-midnight close establishes a day boundary."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Midnight evidence must be timezone aware.")
        value = value.astimezone(UTC)
        if value.hour or value.minute or value.second or value.microsecond:
            raise ValueError("Opening marks must close exactly at UTC midnight.")
        return value


class DailyOpeningEvidence(BaseModel):
    """Reconstructed day equity with fill identity and actual overnight inventory marks.

    Readers revalidate the fill fingerprint and projections. This is derived evidence,
    not permission to rewrite legacy equity stamps or operational cash. A paper futures
    book also reverses its applied funding (``per_product_applied_fills_and_funding_v1``,
    ADR 0129 §4); its fingerprint then covers the funding hours too.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    source: Literal["per_product_applied_fills_v1", "per_product_applied_fills_and_funding_v1"] = (
        "per_product_applied_fills_v1"
    )
    day_start: datetime
    equity: Decimal = Field(allow_inf_nan=False)
    fills_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    marks: tuple[MidnightMark, ...] = ()

    @field_validator("equity", mode="before")
    @classmethod
    def reject_float(cls, value: object) -> object:
        """Do not accept lossy monetary evidence at the storage boundary."""
        if isinstance(value, (float, bool)):
            raise TypeError("Opening equity must be an exact decimal.")
        return value

    @field_validator("day_start")
    @classmethod
    def require_midnight(cls, value: datetime) -> datetime:
        """Use the same exact UTC boundary as opening marks."""
        return MidnightMark.require_midnight(value)

    @model_validator(mode="after")
    def require_matching_marks(self) -> Self:
        """Reject duplicate products and marks from another UTC day."""
        products = [mark.product_id for mark in self.marks]
        if len(products) != len(set(products)) or any(
            mark.closes_at != self.day_start for mark in self.marks
        ):
            raise ValueError("Opening marks must be unique and belong to the evidence day.")
        return self
