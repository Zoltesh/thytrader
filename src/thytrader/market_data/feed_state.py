"""Durable snapshot contracts for the public Coinbase market ticker feed."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MarketFeedUnavailableError(RuntimeError):
    """Signal that durable market-feed state is unavailable."""


class MarketFeedState(StrEnum):
    """Observable public ticker connection state."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    STALE = "stale"
    RECONNECTING = "reconnecting"
    DISABLED = "disabled"


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class MarketFeedSnapshot(_FrozenModel):
    """Latest public ticker lifecycle facts for one USD spot product."""

    product_id: str = Field(pattern=r"^[A-Z0-9]{2,20}-USD$")
    state: MarketFeedState
    last_message_at: datetime | None = None
    last_ticker_at: datetime | None = None
    last_price: Decimal | None = Field(default=None, gt=Decimal("0"))
    updated_at: datetime

    @field_validator("last_message_at", "last_ticker_at", "updated_at")
    @classmethod
    def require_utc_timezone(cls, value: datetime | None) -> datetime | None:
        """Reject naive datetimes so feed age cannot be ambiguous."""
        if value is None:
            return None
        if value.tzinfo is not UTC:
            raise ValueError("datetime must be timezone-aware UTC")
        return value


@runtime_checkable
class MarketFeedStateStore(Protocol):
    """Persist and read the latest public ticker lifecycle snapshot."""

    async def record(self, snapshot: MarketFeedSnapshot) -> None:
        """Replace the latest snapshot for one product."""
        ...

    async def get(self, product_id: str) -> MarketFeedSnapshot | None:
        """Return the latest snapshot, or None when none has been recorded."""
        ...


class DisabledMarketFeedStateStore:
    """Fail closed when durable feed state is unconfigured."""

    async def record(self, snapshot: MarketFeedSnapshot) -> None:
        """Refuse writes so missing storage cannot look healthy."""
        del snapshot
        raise MarketFeedUnavailableError("Market-feed state storage is disabled.")

    async def get(self, product_id: str) -> MarketFeedSnapshot | None:
        """Refuse reads so the API cannot invent feed facts."""
        del product_id
        raise MarketFeedUnavailableError("Market-feed state storage is disabled.")


class InMemoryMarketFeedStateStore:
    """Process-local store used by unit and API tests."""

    def __init__(self) -> None:
        """Initialize an empty snapshot map."""
        self._snapshots: dict[str, MarketFeedSnapshot] = {}

    async def record(self, snapshot: MarketFeedSnapshot) -> None:
        """Store the latest snapshot for the product."""
        self._snapshots[snapshot.product_id] = snapshot

    async def get(self, product_id: str) -> MarketFeedSnapshot | None:
        """Return the stored snapshot when one exists."""
        return self._snapshots.get(product_id)
