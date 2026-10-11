"""Restart-safe product routing without guessing a product from opaque venue ids."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from tests.exchanges.test_coinbase_broker import FakeTransport
from tests.exchanges.test_coinbase_futures_broker import (
    CLIENT,
    HISTORICAL,
    ORDERS,
    PRODUCT,
    Sizes,
    order_json,
)
from thytrader.exchanges.coinbase_broker import CoinbaseRestBroker
from thytrader.exchanges.coinbase_futures_broker import CoinbaseFuturesBroker
from thytrader.execution.broker import BrokerError, ClientOrderLookup
from thytrader.execution.venue_routing import ProductRoutedBroker
from thytrader.trading.models import OrderKind, OrderSide, OrderStatus


def router(spot: CoinbaseRestBroker, futures: CoinbaseFuturesBroker | None) -> ProductRoutedBroker:
    """Construct a fresh router without a place-time cache."""
    return ProductRoutedBroker(spot, futures)


@pytest.mark.anyio
async def test_unavailable_futures_submission_is_definite_no_io_refusal() -> None:
    """No futures adapter must not fall through to the spot adapter."""
    transport = FakeTransport(gets={})
    broker = router(CoinbaseRestBroker(transport), None)
    assert isinstance(broker, ClientOrderLookup)
    result = await broker.place_order(
        client_order_id=CLIENT,
        product_id=PRODUCT,
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.1"),
        price=Decimal("2500"),
    )
    assert result.status is OrderStatus.REJECTED
    assert result.reject_reason == "FUTURES_BROKER_UNAVAILABLE"
    assert transport.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("product", [PRODUCT, "BTC-USD"])
async def test_restart_get_routes_by_authoritative_venue_product(product: str) -> None:
    """A newly constructed router has no submit cache; an opaque id is not a product."""
    row = order_json(product_id=product, product_type="FUTURE" if product == PRODUCT else "SPOT")
    identity = FakeTransport(gets={HISTORICAL: [{"order": row}] * 2})
    futures = FakeTransport(gets={HISTORICAL: [{"order": row}]})
    broker = router(CoinbaseRestBroker(identity), CoinbaseFuturesBroker(futures, Sizes()))
    result = await broker.get_order(venue_order_id="venue-test", client_order_id=CLIENT)
    assert result.filled_quantity == (Decimal("0.2") if product == PRODUCT else Decimal("2"))
    assert len(futures.calls) == (1 if product == PRODUCT else 0)


@pytest.mark.anyio
async def test_restart_client_only_cancel_resolves_venue_id_and_product() -> None:
    """Client-only cancel scans bounded all-product history before one futures cancel."""
    batch = ORDERS + "/historical/batch"
    row = order_json(status="OPEN", filled_size="0")
    identity = FakeTransport(gets={batch: [{"orders": [row], "has_next": False}]})
    futures = FakeTransport(
        gets={HISTORICAL: [{"order": row}, {"order": order_json(status="CANCELLED")}]},
        posts={
            ORDERS + "/batch_cancel": [{"results": [{"order_id": "venue-test", "success": True}]}]
        },
    )
    broker = router(CoinbaseRestBroker(identity), CoinbaseFuturesBroker(futures, Sizes()))
    result = await broker.cancel_order(venue_order_id="", client_order_id=CLIENT)
    assert result.status is OrderStatus.CANCELED
    assert identity.calls == [("GET", batch, {"limit": 100})]
    assert [c for c in futures.calls if c[0] == "POST"] == [
        ("POST", ORDERS + "/batch_cancel", {"order_ids": ["venue-test"]})
    ]


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["get_order", "cancel_order"])
async def test_known_futures_identity_with_missing_adapter_cannot_touch_spot(method: str) -> None:
    """Unknown routing is an observation failure, not a cancellation or spot snapshot."""
    transport = FakeTransport(gets={HISTORICAL: [{"order": order_json()}]})
    broker = router(CoinbaseRestBroker(transport), None)
    with pytest.raises(BrokerError, match="FUTURES_BROKER_UNAVAILABLE"):
        await getattr(broker, method)(venue_order_id="venue-test", client_order_id=CLIENT)
    assert [c[0] for c in transport.calls] == ["GET"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "row",
    [
        order_json(client_order_id="wrong"),
        order_json(product_type="SPOT"),
        order_json(product_id="garbage"),
        {},
    ],
)
async def test_malformed_route_identity_cannot_cancel(row: dict[str, Any]) -> None:
    """Resolve every time; invalid venue identity denies before mutation."""
    transport = FakeTransport(gets={HISTORICAL: [{"order": row}]})
    broker = router(CoinbaseRestBroker(transport), CoinbaseFuturesBroker(transport, Sizes()))
    with pytest.raises(BrokerError):
        await broker.cancel_order(venue_order_id="venue-test", client_order_id=CLIENT)
    assert [c[0] for c in transport.calls] == ["GET"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "pages",
    [
        [{"orders": [], "has_next": True}],
        [{"orders": [], "has_next": True, "cursor": "repeat"}] * 2,
        [{"orders": [], "has_next": True, "cursor": str(i)} for i in range(20)],
        [{"orders": [order_json(), order_json(order_id="other")], "has_next": False}],
    ],
)
async def test_client_route_never_guesses_on_incomplete_or_ambiguous_scan(
    pages: list[dict[str, Any]],
) -> None:
    """Bounded history can fail to resolve; it must never use the wrong broker."""
    transport = FakeTransport(gets={ORDERS + "/historical/batch": pages})
    broker = router(CoinbaseRestBroker(transport), CoinbaseFuturesBroker(transport, Sizes()))
    with pytest.raises(BrokerError):
        await broker.cancel_order(venue_order_id="", client_order_id=CLIENT)
    assert all(call[0] == "GET" for call in transport.calls)
    assert len(transport.calls) <= 20


@pytest.mark.anyio
async def test_routed_futures_lookup_and_fills_use_explicit_product() -> None:
    """Product-bearing methods route directly and retain runtime-checkable lookup."""
    spot = FakeTransport(gets={})
    future = FakeTransport(
        gets={
            ORDERS + "/historical/batch": [{"orders": [], "has_next": False}],
            ORDERS + "/historical/fills": [{"fills": []}],
        }
    )
    broker = router(CoinbaseRestBroker(spot), CoinbaseFuturesBroker(future, Sizes()))
    assert isinstance(broker, ClientOrderLookup)
    assert (
        await broker.find_order_by_client_id(
            client_order_id=CLIENT,
            product_id=PRODUCT,
            submitted_at=datetime(2026, 1, 5, tzinfo=UTC),
        )
        is None
    )
    assert await broker.list_fills(product_id=PRODUCT, order_id="opaque") == ()
    assert spot.calls == []


@pytest.mark.anyio
async def test_spot_submission_preserves_existing_payload_and_units() -> None:
    """Dormant routing must not change a single spot create field or base quantity."""
    spot = FakeTransport(
        posts={ORDERS: [{"success": True, "success_response": {"order_id": "venue-test"}}]},
        gets={HISTORICAL: [{"order": order_json(product_id="BTC-USD", product_type="SPOT")}]},
    )
    broker = router(CoinbaseRestBroker(spot), None)
    result = await broker.place_order(
        client_order_id="legacy-spot-client",
        product_id="BTC-USD",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.2"),
        price=Decimal("2500"),
    )
    assert result.filled_quantity == Decimal("2")
    assert spot.calls[0][2]["order_configuration"] == {
        "limit_limit_gtc": {"base_size": "0.2", "limit_price": "2500", "post_only": True}
    }
