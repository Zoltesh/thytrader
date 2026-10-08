"""Time-sortable identifiers shared across packages.

This module imports nothing from ThyTrader, so strategy authoring can mint ids
without depending on execution. ``thytrader.execution.ids`` re-exports ``uuid7``.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from datetime import datetime


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
