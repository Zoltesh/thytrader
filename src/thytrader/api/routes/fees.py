"""Read-only Coinbase fee tier and transaction cost presentation."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from thytrader.api.dependencies import get_portfolio_service
from thytrader.portfolio.service import (
    PortfolioService,  # noqa: TC001 - FastAPI resolves dependency at runtime.
)

router = APIRouter(prefix="/api/v1/fees", tags=["fees"])
_logger = logging.getLogger(__name__)


class _FrozenResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FeeProfileResponse(_FrozenResponseModel):
    """Exact fee tier rates and 30-day USD volume."""

    taker_fee_rate: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    maker_fee_rate: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    usd_volume_30d: Decimal = Field(ge=Decimal("0"))
    fee_tier: str = Field(min_length=1, max_length=64)
    as_of: datetime
    source: Literal["coinbase"]

    @field_validator("as_of")
    @classmethod
    def require_utc_timezone(cls, value: datetime) -> datetime:
        """Reject naive datetimes to prevent ambiguous fee evaluation times."""
        if value.tzinfo is not UTC:
            raise ValueError("as_of must be timezone-aware UTC")
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
        return FeeProfileResponse(
            taker_fee_rate=profile.taker_fee_rate,
            maker_fee_rate=profile.maker_fee_rate,
            usd_volume_30d=profile.usd_volume_30d,
            fee_tier=profile.fee_tier,
            as_of=profile.as_of,
            source=profile.source,
        )
    except Exception as error:  # noqa: BLE001
        _logger.warning("Failed to serialize fee profile: %s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "fees_unavailable",
                "message": "Fee profile is temporarily unavailable.",
            },
        ) from None
