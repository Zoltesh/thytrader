"""Durable singleton snapshot for the authenticated Coinbase user-order feed."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, field_validator


class UserOrderFeedUnavailableError(RuntimeError):
    """Signal that durable user-order feed state is unavailable."""


class UserOrderFeedState(StrEnum):
    """Observable authenticated user-channel connection state."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    STALE = "stale"
    RECONNECTING = "reconnecting"
    DISABLED = "disabled"


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class UserOrderFeedSnapshot(_FrozenModel):
    """Latest user-order WebSocket lifecycle facts without secrets or payloads."""

    state: UserOrderFeedState
    last_message_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    updated_at: datetime

    @field_validator("last_message_at", "last_heartbeat_at", "updated_at")
    @classmethod
    def require_utc_timezone(cls, value: datetime | None) -> datetime | None:
        """Reject naive datetimes so feed age cannot be ambiguous."""
        if value is None:
            return None
        if value.tzinfo is not UTC:
            raise ValueError("datetime must be timezone-aware UTC")
        return value


@runtime_checkable
class UserOrderFeedStateStore(Protocol):
    """Persist and read the singleton user-order feed lifecycle snapshot."""

    async def record(self, snapshot: UserOrderFeedSnapshot) -> None:
        """Replace the latest singleton snapshot."""
        ...

    async def get(self) -> UserOrderFeedSnapshot | None:
        """Return the latest snapshot, or None when none has been recorded."""
        ...


class DisabledUserOrderFeedStateStore:
    """Fail closed when durable user-order feed state is unconfigured."""

    async def record(self, snapshot: UserOrderFeedSnapshot) -> None:
        """Refuse writes so missing storage cannot look healthy."""
        del snapshot
        raise UserOrderFeedUnavailableError("User-order feed state storage is disabled.")

    async def get(self) -> UserOrderFeedSnapshot | None:
        """Refuse reads so the API cannot invent feed facts."""
        raise UserOrderFeedUnavailableError("User-order feed state storage is disabled.")


class InMemoryUserOrderFeedStateStore:
    """Process-local store used by unit and API tests."""

    def __init__(self) -> None:
        """Initialize with no recorded snapshot."""
        self._snapshot: UserOrderFeedSnapshot | None = None

    async def record(self, snapshot: UserOrderFeedSnapshot) -> None:
        """Store the latest singleton snapshot."""
        self._snapshot = snapshot

    async def get(self) -> UserOrderFeedSnapshot | None:
        """Return the stored snapshot when one exists."""
        return self._snapshot
