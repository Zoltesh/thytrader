"""REST v3 JSON Coinbase broker: pagination, placement source, and fill ledger."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from thytrader.exchanges.coinbase_broker import CoinbaseRestBroker
from thytrader.execution.broker import BrokerError
from thytrader.execution.models import OrderKind, OrderSide, OrderStatus

if TYPE_CHECKING:
    from collections.abc import Mapping


class FakeTransport:
    """Queue JSON responses for documented Advanced Trade paths."""

    def __init__(
        self,
        *,
        gets: dict[str, list[dict[str, Any]]],
        posts: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        """Bind path-keyed FIFO JSON queues."""
        self.gets = gets
        self.posts = posts or {}
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def get(self, path: str, params: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Return the next queued GET body for one path."""
        self.calls.append(("GET", path, dict(params or {})))
        queue = self.gets[path]
        return queue.pop(0)

    def post(self, path: str, data: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Return the next queued POST body for one path."""
        self.calls.append(("POST", path, dict(data or {})))
        queue = self.posts[path]
        return queue.pop(0)


@pytest.mark.anyio
async def test_create_order_uses_client_order_id_and_gets_status() -> None:
    """Create-order JSON is not terminal; the adapter GETs the historical order."""
    transport = FakeTransport(
        posts={
            "/api/v3/brokerage/orders": [
                {"success": True, "order": {"order_id": "venue-1", "status": "PENDING"}}
            ]
        },
        gets={
            "/api/v3/brokerage/orders/historical/venue-1": [
                {
                    "order": {
                        "order_id": "venue-1",
                        "status": "OPEN",
                        "filled_size": "0",
                    }
                }
            ]
        },
    )
    broker = CoinbaseRestBroker(transport)
    result = await broker.place_order(
        client_order_id="client-1",
        product_id="BTC-USD",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.01"),
        price=Decimal("100"),
    )
    assert result.status is OrderStatus.OPEN
    assert result.venue_order_id == "venue-1"
    create_body = transport.calls[0][2]
    assert create_body["client_order_id"] == "client-1"
    assert create_body["order_configuration"]["limit_limit_gtc"]["post_only"] is True


@pytest.mark.anyio
async def test_empty_client_order_id_is_rejected() -> None:
    """SDK-style empty client ids are forbidden so the venue cannot mint a new identity."""
    broker = CoinbaseRestBroker(FakeTransport(gets={}, posts={}))
    with pytest.raises(BrokerError, match="client_order_id"):
        await broker.place_order(
            client_order_id="",
            product_id="BTC-USD",
            side=OrderSide.BUY,
            kind=OrderKind.MARKETABLE,
            quantity=Decimal("0.01"),
            price=Decimal("100"),
        )


@pytest.mark.anyio
async def test_list_fills_paginates_until_has_next_is_false() -> None:
    """Multi-page fill ledgers are concatenated; a missing cursor is an error."""
    path = "/api/v3/brokerage/orders/historical/fills"
    transport = FakeTransport(
        gets={
            path: [
                {
                    "fills": [
                        {
                            "trade_id": "t1",
                            "order_id": "o1",
                            "price": "100",
                            "size": "0.01",
                            "commission": "0.1",
                            "trade_time": "2026-01-01T00:00:00Z",
                        }
                    ],
                    "has_next": True,
                    "cursor": "page-2",
                },
                {
                    "fills": [
                        {
                            "trade_id": "t2",
                            "order_id": "o1",
                            "price": "101",
                            "size": "0.02",
                            "commission": "0",
                            "trade_time": "2026-01-01T01:00:00Z",
                        }
                    ],
                    "has_next": False,
                },
            ]
        }
    )
    fills = await CoinbaseRestBroker(transport).list_fills(product_id="BTC-USD")
    assert [fill.venue_fill_id for fill in fills] == ["t1", "t2"]
    assert transport.calls[1][2]["cursor"] == "page-2"


def test_list_spot_orders_requests_retail_advanced_spot_only() -> None:
    """Bot orders are queried as RETAIL_ADVANCED spot; Simple Trade is not the default."""
    path = "/api/v3/brokerage/orders/historical/batch"
    transport = FakeTransport(
        gets={
            path: [
                {
                    "orders": [{"order_id": "bot-1", "order_placement_source": "RETAIL_ADVANCED"}],
                    "has_next": False,
                }
            ]
        }
    )
    orders = CoinbaseRestBroker(transport).list_spot_orders("BTC-USD")
    assert len(orders) == 1
    params = transport.calls[0][2]
    assert params["order_placement_source"] == "RETAIL_ADVANCED"
    assert params["product_type"] == "SPOT"


@pytest.mark.anyio
async def test_fill_ledger_is_independent_of_open_order_list() -> None:
    """A fill remains visible when the OPEN order list is empty."""
    fills_path = "/api/v3/brokerage/orders/historical/fills"
    orders_path = "/api/v3/brokerage/orders/historical/batch"
    transport = FakeTransport(
        gets={
            orders_path: [{"orders": [], "has_next": False}],
            fills_path: [
                {
                    "fills": [
                        {
                            "trade_id": "hidden-fill",
                            "order_id": "filled-away",
                            "price": "99.5",
                            "size": "0.01",
                            "trade_time": "2026-01-01T00:00:00Z",
                        }
                    ],
                    "has_next": False,
                }
            ],
        }
    )
    broker = CoinbaseRestBroker(transport)
    assert broker.list_spot_orders("BTC-USD") == ()
    fills = await broker.list_fills(product_id="BTC-USD")
    assert fills[0].venue_fill_id == "hidden-fill"
    assert fills[0].venue_order_id == "filled-away"
