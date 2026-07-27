"""Read-only portfolio valuation history HTTP presentation."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic resolves this model field at runtime.
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from thytrader.api.dependencies import get_history_store
from thytrader.api.routes.portfolio import MoneyResponse
from thytrader.persistence.portfolio_history import (
    PortfolioHistoryEntry,
    PortfolioHistoryStore,
    PortfolioHistoryUnavailableError,
)

router = APIRouter(prefix="/api/v1/portfolio", tags=["portfolio"])
_logger = logging.getLogger(__name__)


class PortfolioHistoryEntryResponse(BaseModel):
    """One exact historical portfolio valuation safe for browser display."""

    as_of: datetime
    total_value: MoneyResponse


class PortfolioHistoryResponse(BaseModel):
    """Newest-first portfolio valuation history response."""

    entries: tuple[PortfolioHistoryEntryResponse, ...]


class PersistenceErrorDetail(BaseModel):
    """Stable redacted response for unavailable durable history."""

    code: Literal["persistence_unavailable"]
    message: str


class PersistenceErrorResponse(BaseModel):
    """FastAPI-compatible error envelope for history storage failures."""

    detail: PersistenceErrorDetail


@router.get(
    "/history",
    response_model=PortfolioHistoryResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": PersistenceErrorResponse}},
)
async def get_portfolio_history(
    store: Annotated[PortfolioHistoryStore, Depends(get_history_store)],
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
) -> PortfolioHistoryResponse:
    """Return persisted portfolio valuations or a redacted unavailable response."""
    try:
        entries = await store.list_recent(limit=limit)
    except PortfolioHistoryUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "persistence_unavailable",
                "message": "Portfolio history is unavailable.",
            },
        ) from None
    except Exception as error:  # noqa: BLE001
        _logger.warning("Portfolio history query failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "persistence_unavailable",
                "message": "Portfolio history is unavailable.",
            },
        ) from None
    return PortfolioHistoryResponse(entries=tuple(_to_response(entry) for entry in entries))


def _to_response(entry: PortfolioHistoryEntry) -> PortfolioHistoryEntryResponse:
    """Map one exact domain history entry into a decimal-safe response."""
    return PortfolioHistoryEntryResponse(
        as_of=entry.as_of,
        total_value=MoneyResponse(amount=entry.total_value, currency="USD"),
    )
