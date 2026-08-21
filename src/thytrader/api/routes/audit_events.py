"""Read-only operational audit trail HTTP presentation."""

from __future__ import annotations

from datetime import UTC, datetime
import logging
from typing import Annotated, Literal
from uuid import UUID  # noqa: TC003 - Pydantic resolves model fields at runtime.

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from thytrader.api.dependencies import get_audit_event_store
from thytrader.persistence.audit_events import (
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventStore,
    AuditEventUnavailableError,
)

router = APIRouter(prefix="/api/v1/audit-events", tags=["audit"])
_logger = logging.getLogger(__name__)


class _FrozenResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AuditEventItemResponse(_FrozenResponseModel):
    """One immutable audit trail entry safe for browser display."""

    id: UUID
    occurred_at: datetime
    category: AuditEventCategory
    action: str = Field(min_length=1, max_length=64)
    outcome: AuditEventOutcome
    detail: str
    provider: str | None = None
    product_id: str | None = None
    recorded_at: datetime

    @field_validator("occurred_at", "recorded_at")
    @classmethod
    def require_utc_timezone(cls, value: datetime) -> datetime:
        """Reject naive datetimes so audit ordering cannot be ambiguous."""
        if value.tzinfo is not UTC:
            raise ValueError("timestamp must be timezone-aware UTC")
        return value


class AuditEventListResponse(_FrozenResponseModel):
    """Bounded newest-first audit trail response."""

    events: tuple[AuditEventItemResponse, ...]


class AuditPersistenceErrorDetail(_FrozenResponseModel):
    """Stable redacted response for unavailable audit event storage."""

    code: Literal["persistence_unavailable"]
    message: str


class AuditPersistenceErrorResponse(_FrozenResponseModel):
    """FastAPI-compatible error envelope for audit storage failures."""

    detail: AuditPersistenceErrorDetail


@router.get(
    "",
    response_model=AuditEventListResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": AuditPersistenceErrorResponse}},
)
async def list_audit_events(
    store: Annotated[AuditEventStore, Depends(get_audit_event_store)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> AuditEventListResponse:
    """Return bounded newest-first operational audit trail records."""
    try:
        events = await store.list_recent(limit=limit)
    except (AuditEventUnavailableError, RuntimeError, TypeError, ValueError) as error:
        _logger.warning("Audit event query failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "persistence_unavailable",
                "message": "Audit event storage is unavailable.",
            },
        ) from None
    except Exception as error:  # noqa: BLE001
        _logger.warning("Unexpected audit event query failure: %s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "persistence_unavailable",
                "message": "Audit event storage is unavailable.",
            },
        ) from None

    try:
        response_items = tuple(
            AuditEventItemResponse(
                id=event.id,
                occurred_at=event.occurred_at,
                category=event.category,
                action=event.action,
                outcome=event.outcome,
                detail=event.detail,
                provider=event.provider,
                product_id=event.product_id,
                recorded_at=event.recorded_at,
            )
            for event in events
        )
    except Exception as error:  # noqa: BLE001
        _logger.warning("Failed to serialize audit events: %s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "persistence_unavailable",
                "message": "Audit event storage is unavailable.",
            },
        ) from None

    return AuditEventListResponse(events=response_items)
