"""Bounded normalized broker recording and strict offline scripted adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from thytrader.execution.broker import BrokerError, SubmitResult
from thytrader.replay.broker_events import BrokerEvent, BrokerRequest, Operation
from thytrader.trading.models import Fill, Order, OrderKind, OrderSide

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from decimal import Decimal

    from thytrader.execution.broker import Broker
    from thytrader.market_data.models import Candle

Response = SubmitResult | tuple[Fill, ...] | None
MAX_BROKER_EVENTS = 5000


class BrokerMethods:
    """Explicit broker protocol; dispatch never imports or constructs a network adapter."""

    async def exchange(self, operation: Operation, request: BrokerRequest) -> BrokerEvent:
        """Resolve one normalized asynchronous input in a concrete recording/replay adapter."""
        raise NotImplementedError

    def synchronous(self, operation: Operation, request: BrokerRequest) -> BrokerEvent:
        """Resolve one normalized quote or candle-match input."""
        raise NotImplementedError

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
        """Resolve exactly one recorded submit; unknown responses remain unknown."""
        event = await self.exchange(
            "place",
            BrokerRequest(
                client_order_id=client_order_id,
                product_id=product_id,
                side=side,
                kind=kind,
                quantity=quantity,
                price=price,
                stop_trigger_price=stop_trigger_price,
                take_profit_price=take_profit_price,
            ),
        )
        return require_result(event)

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Resolve a cancellation acknowledgement without retry authority."""
        event = await self.exchange(
            "cancel", BrokerRequest(venue_order_id=venue_order_id, client_order_id=client_order_id)
        )
        return require_result(event)

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Resolve an observation in exact causal order."""
        event = await self.exchange(
            "get", BrokerRequest(venue_order_id=venue_order_id, client_order_id=client_order_id)
        )
        return require_result(event)

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        """Return canonical normalized fills, never synthesized zero-fee executions."""
        event = await self.exchange(
            "fills", BrokerRequest(product_id=product_id, order_id=order_id)
        )
        raise_recorded_error(event)
        return event.fills

    def match_open_order(self, order: Order, candle: Candle) -> Fill | None:
        """Resolve the delegate's actual match behavior, not a new candle simulator."""
        event = self.synchronous("match", BrokerRequest(order=order, candle=candle))
        raise_recorded_error(event)
        return event.fill

    def maker_limit_price(
        self, *, product_id: str, mark: Decimal, side: OrderSide = OrderSide.BUY
    ) -> Decimal:
        """Return the recorded quote, not a contemporary public quote."""
        event = self.synchronous(
            "maker", BrokerRequest(product_id=product_id, mark=mark, side=side)
        )
        raise_recorded_error(event)
        if event.maker_price is None:
            raise ValueError("recorded maker quote missing")
        return event.maker_price


def raise_recorded_error(event: BrokerEvent) -> None:
    """Reproduce only the captured normalized failure category, never raw secret messages."""
    if event.error == "BrokerError":
        raise BrokerError("recorded broker failure")
    if event.error == "TimeoutError":
        raise TimeoutError("recorded broker timeout")
    if event.error == "ValueError":
        raise ValueError("recorded broker refusal")


def require_result(event: BrokerEvent) -> SubmitResult:
    """Require an acknowledgement for create/cancel/get operations."""
    raise_recorded_error(event)
    if event.result is None:
        raise ValueError("recorded acknowledgement missing")
    return event.result


class ScriptedBroker(BrokerMethods):
    """Pure data adapter; unexpected requests fail before any response is consumed."""

    def __init__(self, events: Sequence[BrokerEvent]) -> None:
        """Pin the immutable expected sequence."""
        self.events = tuple(events)
        self._index = 0
        self.failed = False

    @property
    def complete(self) -> bool:
        """Every expected input/response pair must be consumed."""
        return not self.failed and self._index == len(self.events)

    def take(self, operation: Operation, request: BrokerRequest) -> BrokerEvent:
        """Refuse changed requests or event/cause order, including extra sends."""
        if self._index >= len(self.events):
            self.failed = True
            raise ValueError("broker request exceeds recorded input sequence")
        event = self.events[self._index]
        if event.operation != operation or event.request != request:
            self.failed = True
            raise ValueError("broker request or cause order differs from recorded input")
        self._index += 1
        return event

    async def exchange(self, operation: Operation, request: BrokerRequest) -> BrokerEvent:
        """Resolve an asynchronous call from data only."""
        return self.take(operation, request)

    def synchronous(self, operation: Operation, request: BrokerRequest) -> BrokerEvent:
        """Resolve a synchronous call from data only."""
        return self.take(operation, request)


class RecordingBroker(BrokerMethods):
    """Observe a live broker without extra calls, altered acknowledgements or retries."""

    def __init__(self, delegate: Broker) -> None:
        """Keep the existing injected broker, never construct credentials or transports."""
        self.delegate = delegate
        self.events: list[BrokerEvent] = []
        self.failed = False

    def keep(self, event: BrokerEvent) -> None:
        """Bound memory; exhaustion marks incomplete capture but never affects trading."""
        if len(self.events) >= MAX_BROKER_EVENTS:
            self.failed = True
        else:
            self.events.append(event)

    async def record(
        self, operation: Operation, request: BrokerRequest, call: Callable[[], Awaitable[Response]]
    ) -> BrokerEvent:
        """Measure one existing broker operation and retain normalized response/category."""
        started = datetime.now(UTC)
        try:
            response = await call()
        except (BrokerError, TimeoutError, ValueError) as error:
            failure: Literal["BrokerError", "TimeoutError", "ValueError"] = (
                "BrokerError"
                if isinstance(error, BrokerError)
                else "TimeoutError"
                if isinstance(error, TimeoutError)
                else "ValueError"
            )
            self.keep(
                BrokerEvent(
                    operation=operation,
                    request=request,
                    started_at=started,
                    completed_at=datetime.now(UTC),
                    error=failure,
                )
            )
            raise
        event = BrokerEvent(
            operation=operation,
            request=request,
            started_at=started,
            completed_at=datetime.now(UTC),
            result=response if isinstance(response, SubmitResult) else None,
            fills=response if isinstance(response, tuple) else (),
        )
        self.keep(event)
        return event

    async def exchange(self, operation: Operation, request: BrokerRequest) -> BrokerEvent:
        """Route the already requested operation to the existing broker exactly once."""
        if operation == "place":
            submit_client = request.client_order_id
            submit_product = request.product_id
            side = request.side
            kind = request.kind
            quantity = request.quantity
            if (
                submit_client is None
                or submit_product is None
                or side is None
                or kind is None
                or quantity is None
            ):
                raise ValueError("submit input is incomplete")
            return await self.record(
                operation,
                request,
                lambda: self.delegate.place_order(
                    client_order_id=submit_client,
                    product_id=submit_product,
                    side=side,
                    kind=kind,
                    quantity=quantity,
                    price=request.price,
                    stop_trigger_price=request.stop_trigger_price,
                    take_profit_price=request.take_profit_price,
                ),
            )
        if operation == "fills":
            product = request.product_id
            if product is None:
                raise ValueError("fill product is missing")
            return await self.record(
                operation,
                request,
                lambda: self.delegate.list_fills(product_id=product, order_id=request.order_id),
            )
        venue = request.venue_order_id
        client = request.client_order_id
        if venue is None or client is None:
            raise ValueError("order observation identity missing")
        if operation == "cancel":
            return await self.record(
                operation,
                request,
                lambda: self.delegate.cancel_order(venue_order_id=venue, client_order_id=client),
            )
        return await self.record(
            operation,
            request,
            lambda: self.delegate.get_order(venue_order_id=venue, client_order_id=client),
        )

    def synchronous(self, operation: Operation, request: BrokerRequest) -> BrokerEvent:
        """Record actual quote/matching inputs without adding provider calls."""
        started = datetime.now(UTC)
        price = None
        fill = None
        if operation == "maker":
            if request.product_id is None or request.mark is None or request.side is None:
                raise ValueError("maker quote input missing")
            price = self.delegate.maker_limit_price(
                product_id=request.product_id, mark=request.mark, side=request.side
            )
        else:
            if request.order is None or request.candle is None:
                raise ValueError("match input missing")
            fill = self.delegate.match_open_order(request.order, request.candle)
        event = BrokerEvent(
            operation=operation,
            request=request,
            started_at=started,
            completed_at=datetime.now(UTC),
            maker_price=price,
            fill=fill,
        )
        self.keep(event)
        return event
