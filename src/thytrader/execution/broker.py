"""Provider-neutral broker contract used by paper and live runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from thytrader.execution.models import Fill, Order, OrderKind, OrderSide, OrderStatus
    from thytrader.market_data.models import Candle


class BrokerError(RuntimeError):
    """Report a venue or paper-broker failure without leaking credentials."""


@dataclass(frozen=True, slots=True)
class SubmitResult:
    """Immediate venue snapshot after create, cancel, or get."""

    status: OrderStatus
    venue_order_id: str
    filled_quantity: Decimal = Decimal("0")
    reject_reason: str | None = None
    fill_price: Decimal | None = None


class Broker(Protocol):
    """Submit and observe orders without leaking Coinbase SDK types."""

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
        """Submit one order and return the immediate venue-visible snapshot."""
        ...

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Cancel one open order and return its resulting snapshot."""
        ...

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Load one order by venue or client identity."""
        ...

    async def list_fills(
        self,
        *,
        product_id: str,
        order_id: str | None = None,
    ) -> tuple[Fill, ...]:
        """Return paginated fills for one product, optionally narrowed to one order."""
        ...

    def match_open_order(self, order: Order, candle: Candle) -> Fill | None:
        """Return a paper fill when a closed candle trades through an open limit."""
        ...

    def maker_limit_price(self, *, product_id: str, mark: Decimal) -> Decimal:
        """Return the post-only limit price for a long entry."""
        ...
