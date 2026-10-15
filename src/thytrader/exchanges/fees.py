"""Typed domain models for exchange fee tiers and transaction cost visibility."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _unset(value: object) -> bool:
    """Whether an optional tier field is absent."""
    return value is None


class FeeTierBand(_FrozenModel):
    """One Coinbase pricing tier exactly as the account's transaction summary reports it.

    A tier is reached by trailing 30-day spot volume or by assets held on the platform
    (``qualification`` says which applies to this account); either range may be open-ended
    (None) at the top tier (ADR 0144).
    """

    pricing_tier: str = Field(min_length=1, max_length=64)
    maker_fee_rate: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    taker_fee_rate: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    spot_volume_from: Decimal | None = Field(default=None, ge=Decimal("0"))
    spot_volume_to: Decimal | None = Field(default=None, ge=Decimal("0"))
    assets_from: Decimal | None = Field(default=None, ge=Decimal("0"))
    assets_to: Decimal | None = Field(default=None, ge=Decimal("0"))


class FeeProfile(_FrozenModel):
    """Normalized, exact fee tier rates and 30-day volume from Coinbase.

    ``maker_fee_rate`` / ``taker_fee_rate`` are what fills are billed at (promotions
    included). ``current_tier`` / ``next_tier`` and the qualification progress come from the
    same response's tier ladder without promotion, when Coinbase reports it (ADR 0144); no
    schedule is pinned in ThyTrader.
    """

    taker_fee_rate: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    maker_fee_rate: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    usd_volume_30d: Decimal = Field(ge=Decimal("0"))
    fee_tier: str = Field(min_length=1, max_length=64)
    as_of: datetime
    source: Literal["coinbase"] = "coinbase"
    # Omitted while unset, so fee evidence captured before ADR 0144 keeps its bytes.
    current_tier: FeeTierBand | None = Field(default=None, exclude_if=_unset)
    next_tier: FeeTierBand | None = Field(default=None, exclude_if=_unset)
    qualification: str | None = Field(default=None, max_length=64, exclude_if=_unset)
    qualification_value: Decimal | None = Field(default=None, ge=Decimal("0"), exclude_if=_unset)
    next_tier_threshold: Decimal | None = Field(default=None, ge=Decimal("0"), exclude_if=_unset)

    @field_validator("as_of")
    @classmethod
    def require_utc_timezone(cls, value: datetime) -> datetime:
        """Reject naive datetimes to prevent ambiguous timestamps."""
        if value.tzinfo is not UTC:
            raise ValueError("as_of must be timezone-aware UTC")
        return value
