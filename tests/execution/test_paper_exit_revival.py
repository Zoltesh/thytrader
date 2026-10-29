"""A paper take-profit cancelled by its stop's marketable exit stays cancelled."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.execution.test_discretionary import _product
from thytrader.execution.discretionary import place_discretionary_order, process_discretionary_bar
from thytrader.execution.discretionary_request import parse_discretionary_request
from thytrader.execution.paper import PaperBroker
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.models import Candle
from thytrader.market_data.service import MarketDataService
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import OrderKind, OrderSide, OrderStatus, RuntimePhase

# Bars after the placement bar, which is the newest bar closed when the order was placed.
_START = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def _bar(minutes: int, *, low: str, close: str) -> Candle:
    """One closed 5m bar."""
    return Candle(
        starts_at=_START + timedelta(minutes=minutes),
        open=Decimal("100500"),
        high=Decimal("101000"),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("10"),
    )


@pytest.mark.anyio
async def test_a_stopped_paper_desk_book_keeps_its_take_profit_cancelled() -> None:
    """Fill a post-only entry, rest the take-profit, stop out, then reconcile as the worker does.

    The paper broker answers every order lookup with OPEN, so before the fix the worker's
    reconcile revived the cancelled take-profit, which then rested on a flat book.
    """
    store = InMemoryExecutionStore()
    request = parse_discretionary_request(
        mode="paper",
        product_id="BTC-USD",
        entry_kind="post_only_limit",
        limit_price="100000",
        stop_price="95000",
        take_profit_price="110000",
        origin="agent",
        idempotency_key="paper-stop-tp",
        timeframe="5m",
        quantity="0.01",
        paper_starting_cash="10000",
    )
    snapshot = await place_discretionary_order(
        store=store,
        broker=PaperBroker(),
        market_data=MarketDataService(DemoMarketData()),
        request=request,
        live_allowed=False,
    )
    bars: list[Candle] = []
    for bar in (
        _bar(0, low="99900", close="100500"),
        _bar(5, low="100400", close="100500"),
        _bar(10, low="94000", close="94500"),
    ):
        bars.append(bar)
        snapshot = await process_discretionary_bar(
            snapshot, product=_product(), candles=tuple(bars), broker=PaperBroker(), store=store
        )
    assert snapshot.deployment.phase is RuntimePhase.FLAT
    snapshot = await reconcile_open_orders(
        snapshot, broker=PaperBroker(), store=store, product_id="BTC-USD"
    )
    take_profit = next(
        order
        for order in snapshot.orders
        if order.side is OrderSide.SELL and order.kind is OrderKind.POST_ONLY_LIMIT
    )
    assert take_profit.status is OrderStatus.CANCELED
    assert not any(
        order.status in {OrderStatus.OPEN, OrderStatus.PENDING} for order in snapshot.orders
    )
