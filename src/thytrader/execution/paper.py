"""Paper-maker fill matching against closed 1h candles."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.broker import SubmitResult
from thytrader.execution.ids import utc_now, uuid7
from thytrader.execution.models import Fill, Order, OrderKind, OrderSide, OrderStatus

if TYPE_CHECKING:
    from thytrader.market_data.models import Candle


class PaperBroker:
    """Simulate post-only limits and immediate marketable fills without a venue."""

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
        """Accept a paper order; marketable orders fill immediately at the mark price."""
        del product_id, side
        if kind is OrderKind.MARKETABLE:
            if price is None:
                raise ValueError("marketable paper orders require a mark price")
            return SubmitResult(
                status=OrderStatus.FILLED,
                venue_order_id=client_order_id,
                filled_quantity=quantity,
                fill_price=price,
            )
        return SubmitResult(status=OrderStatus.OPEN, venue_order_id=client_order_id)

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Cancel a resting paper order immediately."""
        del client_order_id
        return SubmitResult(status=OrderStatus.CANCELED, venue_order_id=venue_order_id)

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Paper status is local; return the venue id so callers keep persisted rows."""
        del client_order_id
        return SubmitResult(status=OrderStatus.OPEN, venue_order_id=venue_order_id)

    async def list_fills(
        self,
        *,
        product_id: str,
        order_id: str | None = None,
    ) -> tuple[Fill, ...]:
        """Paper fills live in the execution store, not a remote ledger."""
        del product_id, order_id
        return ()

    def maker_limit_price(self, *, product_id: str, mark: Decimal) -> Decimal:
        """Paper maker entries rest at the last closed candle's close."""
        del product_id
        return mark

    def match_open_order(self, order: Order, candle: Candle) -> Fill | None:
        """Fill a resting post-only order when the closed candle trades through its limit."""
        if order.status is not OrderStatus.OPEN or order.price is None:
            return None
        if order.kind is not OrderKind.POST_ONLY_LIMIT:
            return None
        traded_through = (
            candle.low <= order.price if order.side is OrderSide.BUY else candle.high >= order.price
        )
        if not traded_through:
            return None
        return Fill(
            id=uuid7(utc_now()),
            deployment_id=order.deployment_id,
            order_id=order.id,
            venue_fill_id=f"paper:{order.client_order_id}:{candle.starts_at.isoformat()}",
            price=order.price,
            quantity=order.quantity,
            fee=Decimal("0"),
            filled_at=candle.starts_at,
        )
