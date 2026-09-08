"""Provider-neutral exchange account and live-order contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from decimal import Decimal

    from thytrader.exchanges.fees import FeeProfile
    from thytrader.exchanges.models import ExchangeBalance
    from thytrader.execution.broker import SubmitResult
    from thytrader.execution.models import Fill, OrderKind, OrderSide


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

    async def get_fee_profile(self) -> FeeProfile:
        """Return the current 30-day volume and fee tier details."""
        ...


class LiveVenueBroker(Protocol):
    """Submit and observe spot orders through signed REST, never SDK order helpers."""

    async def place_order(
        self,
        *,
        client_order_id: str,
        product_id: str,
        side: OrderSide,
        kind: OrderKind,
        quantity: Decimal,
        price: Decimal | None,
    ) -> SubmitResult:
        """POST one order and return the immediate JSON-derived snapshot."""
        ...

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Cancel one open order and return its resulting snapshot."""
        ...

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """GET one order by venue identity."""
        ...

    async def list_fills(
        self,
        *,
        product_id: str,
        order_id: str | None = None,
    ) -> tuple[Fill, ...]:
        """Page the venue fill ledger for one product, optionally one order."""
        ...

    def maker_limit_price(self, *, product_id: str, mark: Decimal) -> Decimal:
        """Return the post-only limit price for a long entry."""
        ...
