"""Time-sortable identifiers for execution records."""

from __future__ import annotations

from datetime import UTC, datetime
import secrets
from uuid import UUID


def utc_now() -> datetime:
    """Return the current timezone-aware UTC instant."""
    return datetime.now(UTC)


def uuid7(created_at: datetime) -> UUID:
    """Create a UUIDv7 whose timestamp equals the supplied UTC millisecond."""
    milliseconds = int(created_at.timestamp() * 1_000)
    value = (
        (milliseconds << 80)
        | (0x7 << 76)
        | (secrets.randbits(12) << 64)
        | (0b10 << 62)
        | secrets.randbits(62)
    )
    return UUID(int=value)
