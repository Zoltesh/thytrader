"""Dormant product-aware broker facade with restart-safe opaque identity routing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution.broker import (
    BrokerError,
    ClientOrderLookup,
    OrderIdentityLookup,
    SubmitResult,
)
from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.market_data.products import is_spot_product_id
from thytrader.trading.models import OrderSide, OrderStatus

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

    from thytrader.execution.broker import Broker, VenueOrderIdentity
    from thytrader.market_data.models import Candle
    from thytrader.trading.models import Fill, Order, OrderKind


class ProductRoutedBroker:
    """Route known products directly; resolve productless methods from venue evidence.

    The spot adapter must provide OrderIdentityLookup for get/cancel. No place-time
    cache is routing authority. Unknown or incomplete resolution never mutates either
    venue. Generic brokers lacking that optional capability fail closed on these calls.
    """

    def __init__(self, spot: Broker, futures: Broker | None) -> None:
        """Bind dormant adapters without lookup, submission or process wiring."""
        self._spot = spot
        self._futures = futures

    def _for_product(self, product_id: str) -> Broker:
        """Select only validated product classes; never send futures through spot."""
        if is_futures_product_id(product_id):
            if self._futures is None:
                raise BrokerError("FUTURES_BROKER_UNAVAILABLE")
            return self._futures
        if is_spot_product_id(product_id):
            return self._spot
        raise BrokerError("BROKER_PRODUCT_UNSUPPORTED")

    async def place_order(
        self,
        *,
        client_order_id: str,
        product_id: str,
        side: OrderSide,
        kind: OrderKind,
        quantity: Decimal,
        price: Decimal | None,
        stop_trigger_price: Decimal | None = None,
        take_profit_price: Decimal | None = None,
    ) -> SubmitResult:
        """Route the unchanged Broker request; pre-route refusal sends nothing."""
        try:
            broker = self._for_product(product_id)
        except BrokerError as error:
            return SubmitResult(OrderStatus.REJECTED, client_order_id, reject_reason=str(error))
        return await broker.place_order(
            client_order_id=client_order_id,
            product_id=product_id,
            side=side,
            kind=kind,
            quantity=quantity,
            price=price,
            stop_trigger_price=stop_trigger_price,
            take_profit_price=take_profit_price,
        )

    async def _identity(
        self, venue_order_id: str, client_order_id: str
    ) -> VenueOrderIdentity | None:
        """Read authoritative routing metadata on every call, including after restart."""
        if not isinstance(self._spot, OrderIdentityLookup):
            raise BrokerError("Order identity lookup is unavailable.")
        return await self._spot.resolve_order_identity(
            venue_order_id=venue_order_id,
            client_order_id=client_order_id,
        )

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Resolve opaque identities before the selected adapter interprets quantities."""
        identity = await self._identity(venue_order_id, client_order_id)
        if identity is None:
            return SubmitResult(OrderStatus.UNKNOWN, "", reject_reason="not_found")
        return await self._for_product(identity.product_id).get_order(
            venue_order_id=identity.venue_order_id,
            client_order_id=client_order_id,
        )

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Never send a cancel until both venue id and product are authoritatively known."""
        identity = await self._identity(venue_order_id, client_order_id)
        if identity is None:
            raise BrokerError("Cannot cancel an unresolved order.")
        return await self._for_product(identity.product_id).cancel_order(
            venue_order_id=identity.venue_order_id,
            client_order_id=client_order_id,
        )

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        """The explicit product selects the parser; order_id remains an opaque filter."""
        return await self._for_product(product_id).list_fills(
            product_id=product_id, order_id=order_id
        )

    async def find_order_by_client_id(
        self,
        *,
        client_order_id: str,
        product_id: str,
        submitted_at: datetime,
    ) -> SubmitResult | None:
        """Expose runtime-checkable ClientOrderLookup by delegation, not by resubmission."""
        broker = self._for_product(product_id)
        if not isinstance(broker, ClientOrderLookup):
            raise BrokerError("Client order lookup is unavailable.")
        return await broker.find_order_by_client_id(
            client_order_id=client_order_id,
            product_id=product_id,
            submitted_at=submitted_at,
        )

    def match_open_order(self, order: Order, candle: Candle) -> Fill | None:
        """Route paper matching by the persisted order product, never by its venue id."""
        return self._for_product(order.product_id).match_open_order(order, candle)

    def maker_limit_price(
        self,
        *,
        product_id: str,
        mark: Decimal,
        side: OrderSide = OrderSide.BUY,
    ) -> Decimal:
        """Preserve the selected adapter's maker-side pricing semantics."""
        return self._for_product(product_id).maker_limit_price(
            product_id=product_id, mark=mark, side=side
        )
