"""Time-sortable identifiers for execution records.

``uuid7`` lives in ``thytrader.ids`` and is re-exported here (``__all__``).
"""

from __future__ import annotations

from datetime import UTC, datetime

from thytrader.ids import uuid7

__all__ = ["utc_now", "uuid7"]


def utc_now() -> datetime:
    """Return the current timezone-aware UTC instant."""
    return datetime.now(UTC)
