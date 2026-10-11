"""CFM adapter completeness and immutable dormant integration boundary."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from tests.exchanges.test_coinbase_broker import FakeTransport
from tests.exchanges.test_coinbase_futures_broker import CLIENT, PRODUCT, Sizes
from thytrader.exchanges.coinbase_broker import CoinbaseRestBroker
from thytrader.exchanges.coinbase_futures_broker import CoinbaseFuturesBroker
from thytrader.execution.broker import BrokerError
from thytrader.execution.venue_routing import ProductRoutedBroker
from thytrader.market_data.models import Candle
from thytrader.ops_contract import FUTURES_ORDER_PATHS
from thytrader.trading.models import Order, OrderKind, OrderSide, OrderStatus

if TYPE_CHECKING:
    from thytrader.execution.broker import Broker

BOOK = "/api/v3/brokerage/product_book"


@pytest.mark.parametrize("side", list(OrderSide))
def test_futures_maker_price_uses_same_side_book(side: OrderSide) -> None:
    """Entries use bids for buys and asks for sells, never a supplied stale mark."""
    transport = FakeTransport(
        gets={
            BOOK: [
                {
                    "pricebook": {
                        "product_id": PRODUCT,
                        "bids": [{"price": "2499"}],
                        "asks": [{"price": "2501"}],
                    }
                }
            ]
        }
    )
    futures = CoinbaseFuturesBroker(transport, Sizes())
    assert hasattr(futures, "maker_limit_price")
    broker: Broker = ProductRoutedBroker(CoinbaseRestBroker(FakeTransport(gets={})), futures)
    assert broker.maker_limit_price(product_id=PRODUCT, mark=Decimal("100"), side=side) == (
        Decimal("2499") if side is OrderSide.BUY else Decimal("2501")
    )
    assert transport.calls == [("GET", BOOK, {"product_id": PRODUCT, "limit": 1})]


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "0", "-1", 1.1, None])
def test_bad_book_price_never_falls_back_to_mark(value: object) -> None:
    """A malformed book cannot become maker-price evidence."""
    transport = FakeTransport(
        gets={BOOK: [{"pricebook": {"product_id": PRODUCT, "bids": [{"price": value}]}}]}
    )
    futures = CoinbaseFuturesBroker(transport, Sizes())
    assert hasattr(futures, "maker_limit_price")
    with pytest.raises(BrokerError):
        futures.maker_limit_price(product_id=PRODUCT, mark=Decimal("2500"))


def test_futures_has_no_candle_fills() -> None:
    """Broker protocol conformance includes match_open_order; REST owns live fill truth."""
    now = datetime(2026, 1, 5, tzinfo=UTC)
    order = Order(
        id=UUID(int=1),
        deployment_id=UUID(int=2),
        intent_id=UUID(int=3),
        client_order_id=CLIENT,
        product_id=PRODUCT,
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.1"),
        status=OrderStatus.OPEN,
        created_at=now,
        updated_at=now,
    )
    candle = Candle(
        starts_at=now,
        open=Decimal("2500"),
        high=Decimal("2600"),
        low=Decimal("2400"),
        close=Decimal("2500"),
        volume=Decimal("10"),
    )
    futures = CoinbaseFuturesBroker(FakeTransport(gets={}), Sizes())
    assert hasattr(futures, "match_open_order")
    broker: Broker = futures
    assert broker.match_open_order(order, candle) is None
    assert (
        ProductRoutedBroker(CoinbaseRestBroker(FakeTransport(gets={})), futures).match_open_order(
            order, candle
        )
        is None
    )


def test_exact_mutation_path_allowlist_and_dormant_wiring() -> None:
    """No order preview/transfer fallback, no worker/API import, no advertised live path."""
    root = Path(__file__).resolve().parents[2]
    source = root / "src/thytrader/exchanges/coinbase_futures_broker.py"
    constants = {
        node.targets[0].id: node.value.value
        for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    assert {constants[key] for key in ("_ORDERS_PATH", "_CLOSE_PATH", "_CANCEL_PATH")} == {
        "/api/v3/brokerage/orders",
        "/api/v3/brokerage/orders/close_position",
        "/api/v3/brokerage/orders/batch_cancel",
    }
    assert FUTURES_ORDER_PATHS == ()
    for directory in (root / "src/thytrader/api", root / "src/thytrader/execution_worker"):
        for file in directory.rglob("*.py"):
            assert "CoinbaseFuturesBroker" not in file.read_text()
            assert "ProductRoutedBroker" not in file.read_text()
