"""Read-only Coinbase fee tier and transaction cost presentation."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import logging
from typing import TYPE_CHECKING, Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from thytrader.api.dependencies import get_portfolio_service
from thytrader.exchanges.fee_suggestions import suggest_research_fee_rates
from thytrader.exchanges.fees import FeeTierBand
from thytrader.portfolio.service import (
    PortfolioService,
)

if TYPE_CHECKING:
    from thytrader.exchanges.fees import FeeProfile

router = APIRouter(prefix="/api/v1/fees", tags=["fees"])
_logger = logging.getLogger(__name__)


class _FrozenResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FeeProfileResponse(_FrozenResponseModel):
    """Exact fee tier snapshot plus the research/paper maker/taker prefill.

    ``maker_fee_rate`` / ``taker_fee_rate`` are the account's Coinbase snapshot. With
    credentials, ``suggested_*`` equal those account rates (``suggestion_source`` is
    ``coinbase_account``, ADR 0090). ``current_tier`` / ``next_tier`` and the qualification
    progress are Coinbase's own tier ladder for this account (ADR 0144); null when Coinbase
    does not report it. Demo or missing credentials suggest nothing (null).
    """

    taker_fee_rate: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    maker_fee_rate: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    usd_volume_30d: Decimal = Field(ge=Decimal("0"))
    fee_tier: str = Field(min_length=1, max_length=64)
    as_of: datetime
    source: Literal["coinbase"]
    suggested_maker_fee_rate: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("1"))
    suggested_taker_fee_rate: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("1"))
    suggestion_source: Literal["coinbase_account", "unavailable"]
    suggestion_unavailable_reason: Literal["demo_or_missing_credentials"] | None = None
    suggestion_fee_tier: str | None = Field(default=None, min_length=1, max_length=64)
    suggestion_fetched_at: datetime | None = None
    current_tier: FeeTierBand | None = None
    next_tier: FeeTierBand | None = Field(
        default=None, description="The next tier up as Coinbase reports it; null at the top."
    )
    qualification: str | None = Field(
        default=None, description="How this account's tier is reached, e.g. spot_volume."
    )
    qualification_value: Decimal | None = Field(
        default=None, description="The account's current value for that qualification."
    )
    next_tier_threshold: Decimal | None = Field(
        default=None, description="The qualification value at which the next tier starts."
    )

    @field_validator("as_of")
    @classmethod
    def require_utc_timezone(cls, value: datetime) -> datetime:
        """Reject naive datetimes to prevent ambiguous fee evaluation times."""
        if value.tzinfo is not UTC:
            raise ValueError("as_of must be timezone-aware UTC")
        return value

    @field_validator("suggestion_fetched_at")
    @classmethod
    def require_utc_suggestion_time(cls, value: datetime | None) -> datetime | None:
        """Reject naive suggestion timestamps; allow null when no suggestion exists."""
        if value is None:
            return None
        if value.tzinfo is not UTC:
            raise ValueError("suggestion_fetched_at must be timezone-aware UTC")
        return value


class FeeErrorDetail(_FrozenResponseModel):
    """Stable redacted error detail for fee service failures."""

    code: Literal["fees_unavailable"]
    message: str


class FeeErrorResponse(_FrozenResponseModel):
    """FastAPI error envelope for fee retrieval failures."""

    detail: FeeErrorDetail


@router.get(
    "",
    response_model=FeeProfileResponse,
    responses={status.HTTP_502_BAD_GATEWAY: {"model": FeeErrorResponse}},
)
async def get_fee_profile(
    portfolio_service: Annotated[PortfolioService, Depends(get_portfolio_service)],
) -> FeeProfileResponse:
    """Return the current 30-day volume and fee tier."""
    try:
        profile = await portfolio_service.get_fee_profile()
    except (RuntimeError, TypeError, ValueError) as error:
        _logger.warning("Fee profile retrieval failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "fees_unavailable",
                "message": "Fee profile is temporarily unavailable.",
            },
        ) from None
    except Exception as error:  # noqa: BLE001
        _logger.warning("Unexpected fee profile error: %s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "fees_unavailable",
                "message": "Fee profile is temporarily unavailable.",
            },
        ) from None

    try:
        return _fee_profile_response(profile, demo=portfolio_service.demo)
    except Exception as error:  # noqa: BLE001
        _logger.warning("Failed to serialize fee profile: %s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "fees_unavailable",
                "message": "Fee profile is temporarily unavailable.",
            },
        ) from None


def _fee_profile_response(profile: FeeProfile, *, demo: bool) -> FeeProfileResponse:
    """Project a Coinbase snapshot plus research-only suggested maker/taker rates."""
    suggestion = suggest_research_fee_rates(profile=profile, demo=demo)
    return FeeProfileResponse(
        taker_fee_rate=profile.taker_fee_rate,
        maker_fee_rate=profile.maker_fee_rate,
        usd_volume_30d=profile.usd_volume_30d,
        fee_tier=profile.fee_tier,
        as_of=profile.as_of,
        source=profile.source,
        suggested_maker_fee_rate=suggestion.suggested_maker_fee_rate,
        suggested_taker_fee_rate=suggestion.suggested_taker_fee_rate,
        suggestion_source=suggestion.source,
        suggestion_unavailable_reason=suggestion.unavailable_reason,
        suggestion_fee_tier=suggestion.fee_tier,
        suggestion_fetched_at=suggestion.fetched_at,
        current_tier=profile.current_tier,
        next_tier=profile.next_tier,
        qualification=profile.qualification,
        qualification_value=profile.qualification_value,
        next_tier_threshold=profile.next_tier_threshold,
    )
