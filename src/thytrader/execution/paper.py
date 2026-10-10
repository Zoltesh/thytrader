"""Paper-maker fill matching against closed candles."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.broker import SubmitResult
from thytrader.trading.futures_book import current_futures_book
from thytrader.trading.ids import utc_now, uuid7
from thytrader.trading.ledger import (
    PAPER_MAKER_FEE_RATE,
    PAPER_TAKER_FEE_RATE,
    effective_paper_fee_rates,
    paper_fill_fee,
)
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    Fill,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
)

if TYPE_CHECKING:
    from thytrader.execution.broker import Broker
    from thytrader.market_data.models import Candle


_ZERO = Decimal(0)


class PaperBroker:
    """Simulate post-only limits and immediate marketable fills without a venue."""

    def __init__(
        self,
        *,
        maker_fee_rate: Decimal = PAPER_MAKER_FEE_RATE,
        taker_fee_rate: Decimal = PAPER_TAKER_FEE_RATE,
        fee_per_unit: Decimal = _ZERO,
    ) -> None:
        """Bind the documented paper maker/taker schedule used on recorded fills.

        ``fee_per_unit`` is a paper futures book's per-contract fee per base unit (ADR 0129);
        it is zero for spot.
        """
        self.maker_fee_rate = maker_fee_rate
        self.taker_fee_rate = taker_fee_rate
        self.fee_per_unit = fee_per_unit

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
        """Accept a paper order; marketable orders fill immediately at the mark price."""
        del product_id, side, stop_trigger_price, take_profit_price
        if kind is OrderKind.ADOPTION:
            raise ValueError("inventory adoption is never routed to a broker")
        if kind is OrderKind.TRIGGER_BRACKET:
            raise ValueError("paper does not submit venue trigger brackets")
        if kind is OrderKind.STOP_LIMIT:
            raise ValueError("paper does not submit venue stop-limit orders")
        if kind is OrderKind.MARKETABLE:
            if price is None:
                raise ValueError("marketable paper orders require a mark price")
            return SubmitResult(
                status=OrderStatus.FILLED,
                venue_order_id=client_order_id,
                filled_quantity=quantity,
                fill_price=price,
                fill_fee=paper_fill_fee(
                    kind=kind,
                    price=price,
                    quantity=quantity,
                    maker_fee_rate=self.maker_fee_rate,
                    taker_fee_rate=self.taker_fee_rate,
                )
                + quantity * self.fee_per_unit,
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

    def maker_limit_price(
        self, *, product_id: str, mark: Decimal, side: OrderSide = OrderSide.BUY
    ) -> Decimal:
        """Paper maker entries rest at the last closed candle's close."""
        del product_id, side
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
            fee=paper_fill_fee(
                kind=order.kind,
                price=order.price,
                quantity=order.quantity,
                maker_fee_rate=self.maker_fee_rate,
                taker_fee_rate=self.taker_fee_rate,
            )
            + order.quantity * self.fee_per_unit,
            filled_at=candle.starts_at,
        )


def bind_paper_broker_fees(broker: Broker, deployment: Deployment) -> Broker:
    """Bind this paper book's documented maker/taker rates onto a ``PaperBroker``.

    Live books and non-paper brokers are returned unchanged. Rates are modeled
    assumptions, not observed Coinbase fees. A paper futures book also pays its bound
    per-contract fee on every fill (ADR 0129 §4).
    """
    if deployment.mode is not DeploymentMode.PAPER:
        return broker
    if not isinstance(broker, PaperBroker):
        return broker
    maker_fee_rate, taker_fee_rate = effective_paper_fee_rates(
        deployment.paper_maker_fee_rate, deployment.paper_taker_fee_rate
    )
    fee_per_unit = _futures_fee_per_unit(deployment)
    if (
        broker.maker_fee_rate == maker_fee_rate
        and broker.taker_fee_rate == taker_fee_rate
        and broker.fee_per_unit == fee_per_unit
    ):
        return broker
    return PaperBroker(
        maker_fee_rate=maker_fee_rate, taker_fee_rate=taker_fee_rate, fee_per_unit=fee_per_unit
    )


def _futures_fee_per_unit(deployment: Deployment) -> Decimal:
    """The bound per-contract fee per base unit of this futures book, else zero."""
    state = current_futures_book(deployment.id)
    if state is None or state.binding is None:
        return _ZERO
    contract_size = Decimal(state.binding.contract.contract_size)
    return state.binding.fee_per_contract / contract_size
