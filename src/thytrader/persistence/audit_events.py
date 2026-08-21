"""Typed domain models and contracts for append-only audit event recording."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AuditEventUnavailableError(RuntimeError):
    """Signal that durable audit event storage is disabled or unavailable."""


class AuditEventCategory(StrEnum):
    """Broad functional categories for system audit observations."""

    CONNECTION = "connection"
    SNAPSHOT = "snapshot"
    WORKER_ERROR = "worker_error"
    MARKET_DATA = "market_data"
    WEBSOCKET = "websocket"


class AuditEventOutcome(StrEnum):
    """Normalized outcomes for audit actions."""

    SUCCESS = "success"
    FAILURE = "failure"
    INFO = "info"


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class AuditEvent(_FrozenModel):
    """One immutable, timezone-aware UTC audit log record."""

    id: UUID = Field(default_factory=uuid4)
    occurred_at: datetime
    category: AuditEventCategory
    action: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_\-:]+$")
    outcome: AuditEventOutcome
    detail: str = Field(default="", max_length=2048)
    provider: str | None = Field(default=None, max_length=32)
    product_id: str | None = Field(default=None, max_length=32)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("occurred_at", "recorded_at")
    @classmethod
    def require_utc_timezone(cls, value: datetime) -> datetime:
        """Reject naive datetimes to prevent ambiguous event sequencing."""
        if value.tzinfo is not UTC:
            raise ValueError("datetime must be timezone-aware UTC")
        return value


@runtime_checkable
class AuditEventStore(Protocol):
    """Append and query immutable operational audit trail entries."""

    async def append(self, event: AuditEvent) -> None:
        """Persist one audit observation."""
        ...

    async def list_recent(self, *, limit: int = 50) -> tuple[AuditEvent, ...]:
        """Return newest-first bounded audit events."""
        ...


class DisabledAuditEventStore:
    """Preserve system execution while audit event storage is unconfigured."""

    async def append(self, event: AuditEvent) -> None:
        """Deliberately skip recording when storage is disabled."""
        del event

    async def list_recent(self, *, limit: int = 50) -> tuple[AuditEvent, ...]:
        """Reject reads so unconfigured storage fails closed."""
        del limit
        raise AuditEventUnavailableError("Audit event storage is unavailable.")


class InMemoryAuditEventStore:
    """In-memory bounded store used for unit and API isolation tests."""

    def __init__(self, max_capacity: int = 500) -> None:
        """Initialize store with an upper capacity limit."""
        if max_capacity < 1:
            raise ValueError("max_capacity must be positive")
        self._max_capacity = max_capacity
        self._events: list[AuditEvent] = []

    async def append(self, event: AuditEvent) -> None:
        """Store an event, evicting the oldest if capacity is reached."""
        self._events.append(event)
        if len(self._events) > self._max_capacity:
            self._events.pop(0)

    async def list_recent(self, *, limit: int = 50) -> tuple[AuditEvent, ...]:
        """Return newest-first events bounded by limit."""
        if limit < 1:
            raise ValueError("limit must be positive")
        bounded_limit = min(limit, 500)
        return tuple(reversed(self._events[-bounded_limit:]))
