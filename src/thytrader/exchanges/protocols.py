"""Provider-neutral exchange account contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from decimal import Decimal

    from thytrader.exchanges.models import ExchangeBalance


class ExchangeAccount(Protocol):
    """Read-only exchange capabilities used by portfolio aggregation."""

    async def list_balances(self) -> tuple[ExchangeBalance, ...]:
        """Return all non-empty exchange balances."""
        ...

    async def get_permissions(self) -> tuple[str, ...]:
        """Return detected key permissions without enforcing a permission ceiling."""
        ...

    async def get_usd_price(self, currency: str) -> Decimal | None:
        """Return a direct USD spot price when one exists."""
        ...
