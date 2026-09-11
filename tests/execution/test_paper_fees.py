"""Documented paper maker/taker fees on resting and marketable fills."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from thytrader.execution.models import Order, OrderKind, OrderSide, OrderStatus
from thytrader.execution.paper import PaperBroker
from thytrader.market_data.models import Candle


def _order(*, kind: OrderKind, side: OrderSide, price: Decimal) -> Order:
    """Return one open paper order used as a match fixture."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return Order(
        id=uuid4(),
        deployment_id=UUID(int=1),
        intent_id=uuid4(),
        client_order_id="paper-1",
        side=side,
        kind=kind,
        quantity=Decimal("2"),
        status=OrderStatus.OPEN,
        created_at=now,
        updated_at=now,
        price=price,
    )


def _candle(*, low: Decimal, high: Decimal) -> Candle:
    """Return one closed bar whose extremes decide maker matching."""
    start = datetime(2026, 1, 1, 1, tzinfo=UTC)
    return Candle(
        starts_at=start,
        open=Decimal("100"),
        high=high,
        low=low,
        close=Decimal("100"),
        volume=Decimal("10"),
    )


@pytest.mark.anyio
async def test_paper_marketable_place_records_taker_fee() -> None:
    """Immediate marketable paper fills charge the documented taker rate."""
    result = await PaperBroker().place_order(
        client_order_id="mkt-1",
        product_id="BTC-USD",
        side=OrderSide.SELL,
        kind=OrderKind.MARKETABLE,
        quantity=Decimal("2"),
        price=Decimal("100"),
    )
    assert result.fill_price == Decimal("100")
    assert result.fill_fee == Decimal("0.4")


def test_paper_maker_match_records_maker_fee() -> None:
    """A buy that trades through its limit records maker fee at the posted price."""
    order = _order(kind=OrderKind.POST_ONLY_LIMIT, side=OrderSide.BUY, price=Decimal("100"))
    fill = PaperBroker().match_open_order(order, _candle(low=Decimal("99"), high=Decimal("101")))
    assert fill is not None
    assert fill.price == Decimal("100")
    assert fill.fee == Decimal("0.2")


def test_paper_maker_match_does_not_fill_when_low_stays_above_limit() -> None:
    """Fees are not invented for an unfilled resting order."""
    order = _order(kind=OrderKind.POST_ONLY_LIMIT, side=OrderSide.BUY, price=Decimal("100"))
    fill = PaperBroker().match_open_order(order, _candle(low=Decimal("100.5"), high=Decimal("102")))
    assert fill is None
