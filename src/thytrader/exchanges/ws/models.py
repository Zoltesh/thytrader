"""Typed domain models and contracts for Coinbase WebSocket market ticker streaming."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WebSocketConnectionState(StrEnum):
    """Lifecycle state of the WebSocket market feed."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    STALE = "stale"
    RECONNECTING = "reconnecting"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TickerMessage(_FrozenModel):
    """Normalized real-time market ticker tick for a spot product."""

    product_id: str = Field(pattern=r"^[A-Z0-9]{2,20}-USD$")
    price: Decimal = Field(gt=Decimal("0"))
    volume_24_h: Decimal = Field(ge=Decimal("0"))
    low_24_h: Decimal = Field(ge=Decimal("0"))
    high_24_h: Decimal = Field(ge=Decimal("0"))
    low_52_w: Decimal = Field(ge=Decimal("0"))
    high_52_w: Decimal = Field(ge=Decimal("0"))
    price_percent_chg_24_h: Decimal
    time: datetime

    @field_validator("time")
    @classmethod
    def require_utc_timezone(cls, value: datetime) -> datetime:
        """Reject naive datetimes."""
        if value.tzinfo is not UTC:
            raise ValueError("time must be timezone-aware UTC")
        return value


class HeartbeatMessage(_FrozenModel):
    """Heartbeat message to verify connection liveness."""

    current_time: datetime
    heartbeat_counter: int = Field(ge=0)

    @field_validator("current_time")
    @classmethod
    def require_utc_timezone(cls, value: datetime) -> datetime:
        """Reject naive datetimes."""
        if value.tzinfo is not UTC:
            raise ValueError("current_time must be timezone-aware UTC")
        return value
