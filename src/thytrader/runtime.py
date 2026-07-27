"""Process-local runtime state shared by API dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from thytrader.config import Settings


@dataclass(slots=True)
class RuntimeState:
    """Track immutable settings and mutable process-readiness state."""

    settings: Settings
    ready: bool = False
