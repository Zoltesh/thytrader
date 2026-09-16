"""REST v3 JSON Coinbase broker: pagination, placement source, and fill ledger."""

from __future__ import annotations

import asyncio
from decimal import Decimal
import time
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
    assert "attached_order_configuration" not in create_body


@pytest.mark.anyio
async def test_create_order_attaches_trigger_bracket_without_child_size() -> None:
    """Attached entry brackets omit size so the child inherits the parent fill."""
    transport = FakeTransport(
        posts={
            "/api/v3/brokerage/orders": [
                {"success": True, "order": {"order_id": "venue-attached", "status": "PENDING"}}
            ]
        },
        gets={
            "/api/v3/brokerage/orders/historical/venue-attached": [
                {
                    "order": {
                        "order_id": "venue-attached",
                        "status": "OPEN",
                        "filled_size": "0",
                    }
                }
            ]
        },
    )
    broker = CoinbaseRestBroker(transport)
    result = await broker.place_order(
        client_order_id="client-attached",
        product_id="BTC-USD",
        side=OrderSide.SELL,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.01"),
        price=Decimal("100"),
        stop_trigger_price=Decimal("110"),
        take_profit_price=Decimal("90"),
    )
    assert result.status is OrderStatus.OPEN
    create_body = transport.calls[0][2]
    assert create_body["side"] == "SELL"
    attached = create_body["attached_order_configuration"]["trigger_bracket_gtc"]
    assert attached["limit_price"] == "90"
    assert attached["stop_trigger_price"] == "110"
    assert "base_size" not in attached
    assert "size" not in attached
    assert "leverage" not in create_body
    assert "margin_type" not in create_body


@pytest.mark.anyio
async def test_create_trigger_bracket_uses_limit_and_stop_trigger() -> None:
    """Live OCO submits Advanced Trade trigger_bracket_gtc JSON."""
    transport = FakeTransport(
        posts={
            "/api/v3/brokerage/orders": [
                {"success": True, "order": {"order_id": "venue-oco", "status": "PENDING"}}
            ]
        },
        gets={
            "/api/v3/brokerage/orders/historical/venue-oco": [
                {
                    "order": {
                        "order_id": "venue-oco",
                        "status": "OPEN",
                        "filled_size": "0",
                    }
                }
            ]
        },
    )
    broker = CoinbaseRestBroker(transport)
    result = await broker.place_order(
        client_order_id="client-oco",
        product_id="BTC-USD",
        side=OrderSide.SELL,
        kind=OrderKind.TRIGGER_BRACKET,
        quantity=Decimal("0.01"),
        price=Decimal("120"),
        stop_trigger_price=Decimal("90"),
    )
    assert result.status is OrderStatus.OPEN
    create_body = transport.calls[0][2]
    configuration = create_body["order_configuration"]["trigger_bracket_gtc"]
    assert configuration["limit_price"] == "120"
    assert configuration["stop_trigger_price"] == "90"
    assert configuration["base_size"] == "0.01"


@pytest.mark.anyio
async def test_create_order_reads_success_response_order_id() -> None:
    """Advanced Trade create-order JSON nests the venue id under success_response."""
    transport = FakeTransport(
        posts={
            "/api/v3/brokerage/orders": [
                {
                    "success": True,
                    "success_response": {
                        "order_id": "venue-1",
                        "product_id": "BTC-USD",
                        "side": "BUY",
                        "client_order_id": "client-1",
                    },
                }
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


@pytest.mark.anyio
async def test_get_order_resolves_client_order_id_when_venue_id_is_missing() -> None:
    """Ambiguous submits are looked up from historical spot orders by client id."""
    batch = "/api/v3/brokerage/orders/historical/batch"
    historical = "/api/v3/brokerage/orders/historical/venue-9"
    transport = FakeTransport(
        gets={
            batch: [
                {
                    "orders": [
                        {
                            "order_id": "venue-9",
                            "client_order_id": "client-missing-venue",
                            "status": "OPEN",
                        }
                    ],
                    "has_next": False,
                }
            ],
            historical: [
                {
                    "order": {
                        "order_id": "venue-9",
                        "status": "OPEN",
                        "filled_size": "0",
                    }
                }
            ],
        }
    )
    result = await CoinbaseRestBroker(transport).get_order(
        venue_order_id="", client_order_id="client-missing-venue"
    )
    assert result.venue_order_id == "venue-9"
    assert result.status is OrderStatus.OPEN
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


class _SlowTransport:
    """A transport whose GET/POST block the calling thread, not the event loop."""

    def __init__(self, *, delay_seconds: float, gets: dict[str, dict[str, Any]]) -> None:
        """Bind one fixed-delay JSON body per GET path."""
        self._delay_seconds = delay_seconds
        self._gets = gets

    def get(self, path: str, params: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Block the calling thread for the configured delay, then return the queued body."""
        del params
        time.sleep(self._delay_seconds)
        return self._gets[path]

    def post(self, path: str, data: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Unused by these tests; present to satisfy the transport protocol."""
        del path, data
        raise AssertionError("post() is unused by this fake.")


@pytest.mark.anyio
async def test_get_order_offloads_the_blocking_transport_call() -> None:
    """A slow GET must run concurrently with other event-loop work, not stall it (F16).

    A blocking `time.sleep` left on the event loop thread would serialize this GET
    with the concurrent ticker below, so the combined wall-clock time would be close
    to their sum. Offloading the GET to a worker thread lets both run concurrently,
    so the combined time stays close to the slower of the two instead.
    """
    delay_seconds = 0.2
    transport = _SlowTransport(
        delay_seconds=delay_seconds,
        gets={
            "/api/v3/brokerage/orders/historical/venue-1": {
                "order": {"order_id": "venue-1", "status": "OPEN", "filled_size": "0"}
            }
        },
    )
    broker = CoinbaseRestBroker(transport)
    ticks = 0

    async def _tick_while_waiting() -> None:
        nonlocal ticks
        for _ in range(8):
            await asyncio.sleep(delay_seconds / 8)
            ticks += 1

    started_at = time.monotonic()
    result, _ = await asyncio.gather(
        broker.get_order(venue_order_id="venue-1", client_order_id="client-1"),
        _tick_while_waiting(),
    )
    elapsed = time.monotonic() - started_at
    assert result.status is OrderStatus.OPEN
    assert ticks == 8
    assert elapsed < delay_seconds * 1.5
