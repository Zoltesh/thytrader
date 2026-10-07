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


@dataclass(frozen=True, slots=True)
class ExchangeOpenOrder:
    """One venue-resting order observed through a read-only listing.

    Only fields the reconciliation report needs are kept; venue account or profile
    identifiers are deliberately absent so listings can be shown to operators.
    """

    venue_order_id: str
    product_id: str | None = None
    side: str | None = None
    status: str | None = None
    client_order_id: str | None = None
