"""Portfolio HTTP presentation and redacted failure handling."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic resolves this model field at runtime.
from decimal import Decimal  # noqa: TC003 - Pydantic resolves and serializes this at runtime.
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, field_serializer

from thytrader.api.dependencies import get_portfolio_service
from thytrader.portfolio.models import Portfolio  # noqa: TC001 - resolved by Pydantic at runtime.
from thytrader.portfolio.service import (  # noqa: TC001 - resolved by FastAPI Depends at runtime.
    PortfolioService,
)

router = APIRouter(prefix="/api/v1", tags=["portfolio"])


class MoneyResponse(BaseModel):
    """Exact money serialized as a decimal string."""

    model_config = ConfigDict(from_attributes=True)
    amount: Decimal
    currency: Literal["USD"]

    @field_serializer("amount")
    def serialize_amount(self, value: Decimal) -> str:
        """Serialize exact monetary values without binary floating point."""
        return format(value, "f")


class PortfolioAssetResponse(BaseModel):
    """One balance and its optional USD valuation."""

    model_config = ConfigDict(from_attributes=True)
    currency: str
    name: str
    available: Decimal
    hold: Decimal
    total: Decimal
    value: MoneyResponse | None

    @field_serializer("available", "hold", "total")
    def serialize_quantity(self, value: Decimal) -> str:
        """Serialize exchange quantities as exact decimal strings."""
        return format(value, "f")


class ConnectionResponse(BaseModel):
    """Coinbase connection state safe for browser display."""

    model_config = ConfigDict(from_attributes=True)
    provider: Literal["coinbase"]
    status: Literal["connected", "demo"]
    permissions: tuple[str, ...]


class PortfolioResponse(BaseModel):
    """Browser-safe point-in-time portfolio response."""

    as_of: datetime
    connection: ConnectionResponse
    demo: bool
    total_value: MoneyResponse
    assets: tuple[PortfolioAssetResponse, ...]
    unvalued_assets: tuple[str, ...]


class ErrorDetail(BaseModel):
    """Stable redacted API failure body."""

    code: Literal["coinbase_unavailable"]
    message: str


class ErrorResponse(BaseModel):
    """FastAPI-compatible error envelope returned to browser clients."""

    detail: ErrorDetail


@router.get(
    "/portfolio",
    response_model=PortfolioResponse,
    responses={
        status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
    },
)
async def get_portfolio(
    service: Annotated[PortfolioService, Depends(get_portfolio_service)],
) -> PortfolioResponse:
    """Return the current Coinbase or demo portfolio without exposing credentials."""
    try:
        portfolio = await service.get_portfolio()
    except Exception:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "coinbase_unavailable",
                "message": (
                    "Coinbase is temporarily unavailable. Check your credentials and try again."
                ),
            },
        ) from None
    return _to_response(portfolio)


def _to_response(portfolio: Portfolio) -> PortfolioResponse:
    """Map an exact domain snapshot to its browser-safe response."""
    return PortfolioResponse(
        as_of=portfolio.as_of,
        connection=ConnectionResponse.model_validate(portfolio.connection),
        demo=portfolio.demo,
        total_value=MoneyResponse.model_validate(portfolio.total_value),
        assets=tuple(PortfolioAssetResponse.model_validate(asset) for asset in portfolio.assets),
        unvalued_assets=portfolio.unvalued_assets,
    )
