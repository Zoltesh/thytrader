"""Read-only historical market-data preview HTTP presentation."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic resolves this model field at runtime.
from decimal import Decimal  # noqa: TC003 - Pydantic resolves this model field at runtime.
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, field_serializer

from thytrader.api.dependencies import get_market_data_service
from thytrader.market_data.models import (
    MarketDataPreview,  # noqa: TC001 - Pydantic resolves this model at runtime.
)
from thytrader.market_data.service import (  # noqa: TC001 - FastAPI resolves Depends at runtime.
    MarketDataService,
)

router = APIRouter(prefix="/api/v1/market-data", tags=["market-data"])


class ProductResponse(BaseModel):
    """Browser-safe Coinbase spot-product constraints with exact decimal serialization."""

    model_config = ConfigDict(from_attributes=True)

    product_id: str
    base_currency: str
    quote_currency: str
    price_increment: Decimal
    base_increment: Decimal
    quote_increment: Decimal
    base_min_size: Decimal
    quote_min_size: Decimal
    trading_enabled: bool

    @field_serializer(
        "price_increment",
        "base_increment",
        "quote_increment",
        "base_min_size",
        "quote_min_size",
    )
    def serialize_decimal(self, value: Decimal) -> str:
        """Preserve exact venue constraints across the browser API boundary."""
        return format(value, "f")


class MarketDataQualityResponse(BaseModel):
    """Observable recent-candle completeness and freshness facts."""

    candle_count: int
    gap_count: int
    missing_intervals: int
    latest_completed_at: datetime | None
    stale: bool


class MarketDataPreviewResponse(BaseModel):
    """Browser-safe current market-data preview for the supported product/timeframe."""

    as_of: datetime
    product: ProductResponse
    timeframe: Literal["1h"]
    quality: MarketDataQualityResponse


class MarketDataErrorDetail(BaseModel):
    """Stable redacted market-data upstream failure detail."""

    code: Literal["market_data_unavailable"]
    message: str


class MarketDataErrorResponse(BaseModel):
    """FastAPI-compatible market-data failure envelope."""

    detail: MarketDataErrorDetail


@router.get(
    "/preview",
    response_model=MarketDataPreviewResponse,
    responses={
        status.HTTP_502_BAD_GATEWAY: {"model": MarketDataErrorResponse},
    },
)
async def get_market_data_preview(
    service: Annotated[MarketDataService, Depends(get_market_data_service)],
) -> MarketDataPreviewResponse:
    """Return validated BTC-USD hourly data facts without exposing upstream exceptions."""
    try:
        preview = await service.get_btc_usd_hourly_preview()
    except Exception:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "market_data_unavailable",
                "message": "Market data is temporarily unavailable. Try again shortly.",
            },
        ) from None
    return _to_response(preview)


def _to_response(preview: MarketDataPreview) -> MarketDataPreviewResponse:
    """Map one validated domain preview into its compact browser representation."""
    return MarketDataPreviewResponse(
        as_of=preview.as_of,
        product=ProductResponse.model_validate(preview.product),
        timeframe=preview.interval.value,
        quality=MarketDataQualityResponse(
            candle_count=preview.quality.candle_count,
            gap_count=preview.quality.gap_count,
            missing_intervals=preview.quality.missing_intervals,
            latest_completed_at=preview.quality.latest_completed_at,
            stale=preview.quality.is_stale,
        ),
    )
