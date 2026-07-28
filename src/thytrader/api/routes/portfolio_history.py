"""Read-only portfolio valuation history HTTP presentation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from thytrader.api.dependencies import get_history_store, get_runtime_state
from thytrader.api.routes.portfolio import MoneyResponse
from thytrader.persistence.portfolio_history import (
    PortfolioHistoryEntry,
    PortfolioHistoryStore,
    PortfolioHistoryUnavailableError,
)
from thytrader.runtime import (
    RuntimeState,  # noqa: TC001 - FastAPI resolves this dependency annotation at runtime.
)

router = APIRouter(prefix="/api/v1/portfolio", tags=["portfolio"])
_logger = logging.getLogger(__name__)
HistoryRange = Literal["24h", "7d", "30d", "all"]
_MAX_HISTORY_ENTRIES = 300


class PortfolioHistoryEntryResponse(BaseModel):
    """One exact historical portfolio valuation safe for browser display."""

    as_of: datetime
    total_value: MoneyResponse


class PortfolioHistoryResponse(BaseModel):
    """Bounded newest-first portfolio valuation history response."""

    entries: tuple[PortfolioHistoryEntryResponse, ...]
    range: HistoryRange
    sampling_interval_seconds: int


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
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
    history_range: Annotated[HistoryRange, Query(alias="range")] = "24h",
) -> PortfolioHistoryResponse:
    """Return bounded persisted valuations for one selected presentation range."""
    start = _range_start(history_range, now=datetime.now(UTC))
    try:
        entries = await store.list_range(start=start, max_entries=_MAX_HISTORY_ENTRIES)
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
    return PortfolioHistoryResponse(
        entries=tuple(_to_response(entry) for entry in entries),
        range=history_range,
        sampling_interval_seconds=runtime.settings.snapshot_interval_seconds,
    )


def _to_response(entry: PortfolioHistoryEntry) -> PortfolioHistoryEntryResponse:
    """Map one exact domain history entry into a decimal-safe response."""
    return PortfolioHistoryEntryResponse(
        as_of=entry.as_of,
        total_value=MoneyResponse(amount=entry.total_value, currency="USD"),
    )


def _range_start(history_range: HistoryRange, *, now: datetime) -> datetime | None:
    """Translate a browser range into an explicit UTC lower bound for storage."""
    duration_by_range = {
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
    }
    duration = duration_by_range.get(history_range)
    return now - duration if duration is not None else None
