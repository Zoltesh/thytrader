"""Typed domain models for exchange fee tiers and transaction cost visibility."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class FeeProfile(_FrozenModel):
    """Normalized, exact fee tier rates and 30-day volume from Coinbase."""

    taker_fee_rate: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    maker_fee_rate: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    usd_volume_30d: Decimal = Field(ge=Decimal("0"))
    fee_tier: str = Field(min_length=1, max_length=64)
    as_of: datetime
    source: Literal["coinbase"] = "coinbase"

    @field_validator("as_of")
    @classmethod
    def require_utc_timezone(cls, value: datetime) -> datetime:
        """Reject naive datetimes to prevent ambiguous timestamps."""
        if value.tzinfo is not UTC:
            raise ValueError("as_of must be timezone-aware UTC")
        return value
