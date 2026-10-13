"""Time-sortable identifiers for execution records.

``uuid7`` lives in ``thytrader.ids`` and is re-exported here (``__all__``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.ids import uuid7
from thytrader.runtime_inputs import resolve_time

if TYPE_CHECKING:
    from datetime import datetime

__all__ = ["utc_now", "uuid7"]


def utc_now() -> datetime:
    """Return the current timezone-aware UTC instant."""
    return resolve_time()
