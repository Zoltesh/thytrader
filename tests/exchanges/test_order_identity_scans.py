"""Complete client scans require every row to carry classifiable identity evidence."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

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
from tests.execution.test_reconcile import _snapshot_with_order
from tests.execution.test_unconfirmed_submit_recovery import _unknown_order
from thytrader.exchanges.coinbase_broker import CoinbaseRestBroker
from thytrader.exchanges.coinbase_futures_broker import CoinbaseFuturesBroker
from thytrader.execution.broker import BrokerError
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.venue_routing import ProductRoutedBroker
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentStatus, OrderStatus

BATCH = ORDERS + "/historical/batch"
# Any is restricted to deliberately malformed external JSON in these boundary tests.
BAD_ROWS: list[dict[str, Any]] = [
    {},
    {"client_order_id": None},
    {"client_order_id": 42},
    {"client_order_id": []},
    order_json(client_order_id=None),
    order_json(client_order_id=False),
    order_json(client_order_id=""),
    order_json(client_order_id="unrelated", order_id=None),
    order_json(client_order_id="unrelated", order_id=""),
    order_json(client_order_id="unrelated", product_id=None),
    order_json(client_order_id="unrelated", product_id="bad-product"),
    order_json(client_order_id="unrelated", product_type="SPOT"),
]


@pytest.mark.anyio
@pytest.mark.parametrize("row", BAD_ROWS)
async def test_unclassifiable_nonmatch_is_not_absence(row: dict[str, Any]) -> None:
    """Missing, nonstring or inconsistent identity cannot establish complete absence."""
    transport = FakeTransport(gets={BATCH: [{"orders": [row], "has_next": False}]})
    broker = CoinbaseFuturesBroker(transport, Sizes())
    with pytest.raises(BrokerError):
        await broker.find_order_by_client_id(
            client_order_id=CLIENT,
            product_id=PRODUCT,
            submitted_at=datetime(2026, 1, 5, tzinfo=UTC),
        )
    assert [call[0] for call in transport.calls] == ["GET"]


@pytest.mark.anyio
@pytest.mark.parametrize("row", BAD_ROWS)
@pytest.mark.parametrize("position", ["before", "after", "next-page"])
async def test_unclassifiable_nonmatch_cannot_authorize_cancel(
    row: dict[str, Any], position: str
) -> None:
    """Even a valid match is provisional until every page/row proves uniqueness."""
    match = order_json(status="OPEN", filled_size="0")
    pages = (
        [
            {"orders": [match], "has_next": True, "cursor": "next"},
            {"orders": [row], "has_next": False},
        ]
        if position == "next-page"
        else [{"orders": [row, match] if position == "before" else [match, row], "has_next": False}]
    )
    identity = FakeTransport(gets={BATCH: pages})
    futures = FakeTransport(
        gets={HISTORICAL: [{"order": match}, {"order": {**match, "status": "CANCELLED"}}]},
        posts={
            ORDERS + "/batch_cancel": [{"results": [{"order_id": "venue-test", "success": True}]}]
        },
    )
    broker = ProductRoutedBroker(
        CoinbaseRestBroker(identity), CoinbaseFuturesBroker(futures, Sizes())
    )
    with pytest.raises(BrokerError):
        await broker.cancel_order(venue_order_id="", client_order_id=CLIENT)
    assert futures.calls == []
    assert all(call[0] == "GET" for call in identity.calls)


@pytest.mark.anyio
async def test_bad_nonmatch_after_match_keeps_real_reconcile_unknown() -> None:
    """The existing restart path pauses instead of treating partial scan evidence as recovery."""
    store = InMemoryExecutionStore()
    order = replace(_unknown_order(UUID(int=7001), client_order_id=CLIENT), product_id=PRODUCT)
    deployment = await _snapshot_with_order(store, order)
    await store.save_deployment(replace(deployment, product_id=PRODUCT))
    transport = FakeTransport(
        gets={
            BATCH: [
                {"orders": [order_json()], "has_next": True, "cursor": "next"},
                {"orders": [{}], "has_next": False},
            ],
            HISTORICAL: [{"order": order_json(status="OPEN", filled_size="0")}],
            ORDERS + "/historical/fills": [{"fills": []}],
        }
    )
    spot = FakeTransport(gets={})
    broker = ProductRoutedBroker(
        CoinbaseRestBroker(spot), CoinbaseFuturesBroker(transport, Sizes())
    )
    result = await reconcile_open_orders(
        await store.get_deployment(deployment.id), broker=broker, store=store
    )
    assert result.deployment.status is DeploymentStatus.PAUSED
    assert result.orders[0].status is OrderStatus.UNKNOWN
    assert result.orders[0].venue_order_id is None
    assert result.orders[0].venue_observed_at is None
    assert [call[1] for call in transport.calls] == [BATCH, BATCH]
    assert spot.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("has_match", [False, True])
async def test_valid_unrelated_rows_allow_complete_scan(has_match: bool) -> None:
    """Explicit valid identities may be excluded by exact client id, across products/pages."""
    pages = [
        {
            "orders": [
                order_json(client_order_id="other", product_id="BTC-USD", product_type="SPOT")
            ],
            "has_next": True,
            "cursor": "next",
        },
        {"orders": [order_json()] if has_match else [], "has_next": False},
    ]
    transport = FakeTransport(gets={BATCH: pages})
    result = await CoinbaseRestBroker(transport).resolve_order_identity(
        venue_order_id="", client_order_id=CLIENT
    )
    assert (result is not None) is has_match
    if result is not None:
        assert result.venue_order_id == "venue-test" and result.product_id == PRODUCT
    assert len(transport.calls) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["duplicate", "cursor-cycle", "page-cap"])
async def test_found_identity_never_short_circuits_scan_bounds(failure: str) -> None:
    """An early match cannot bypass later duplicate detection or pagination safeguards."""
    pages: list[dict[str, Any]] = [{"orders": [order_json()], "has_next": True, "cursor": "a"}]
    if failure == "duplicate":
        pages.append({"orders": [order_json(order_id="different")], "has_next": False})
    elif failure == "cursor-cycle":
        pages.extend({"orders": [], "has_next": True, "cursor": cursor} for cursor in ("b", "a"))
    else:
        pages.extend({"orders": [], "has_next": True, "cursor": str(i)} for i in range(19))
    transport = FakeTransport(gets={BATCH: pages})
    broker = ProductRoutedBroker(CoinbaseRestBroker(transport), None)
    with pytest.raises(BrokerError):
        await broker.cancel_order(venue_order_id="", client_order_id=CLIENT)
    assert len(transport.calls) == {"duplicate": 2, "cursor-cycle": 3, "page-cap": 20}[failure]
    assert all(call[0] == "GET" for call in transport.calls)
