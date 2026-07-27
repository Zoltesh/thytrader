"""Provider-neutral exchange account models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ExchangeBalance:
    """One exchange asset balance expressed with exact decimal quantities."""

    currency: str
    name: str
    available: Decimal
    hold: Decimal

    @property
    def total(self) -> Decimal:
        """Return the total quantity across available and held funds."""
        return self.available + self.hold
