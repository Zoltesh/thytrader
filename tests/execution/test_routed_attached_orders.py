"""Persisted spot attachments survive fresh routing without fabricated client echoes."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tests.exchanges.test_coinbase_broker import FakeTransport
from tests.exchanges.test_coinbase_futures_broker import CLIENT, ORDERS, PRODUCT, Sizes, order_json
from tests.execution.test_attached import _deployment
from tests.execution.test_unconfirmed_submit_recovery import _candle
from thytrader.exchanges.coinbase_broker import CoinbaseRestBroker
from thytrader.exchanges.coinbase_futures_broker import CoinbaseFuturesBroker
from thytrader.execution.broker import BrokerError
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.runtime_ops import _cancel_one_order, _cancel_open_orders
from thytrader.execution.submit import submit_intent
from thytrader.execution.venue_routing import ProductRoutedBroker
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    DeploymentStatus,
    IntentPurpose,
    OrderKind,
    OrderSide,
    OrderStatus,
)


@pytest.mark.anyio
@pytest.mark.parametrize("learn_at_submit", [True, False])
@pytest.mark.parametrize("cancel_first", [True, False])
async def test_attached_child_survives_restart_reconcile_and_cancel(
    learn_at_submit: bool, cancel_first: bool
) -> None:
    """Use actual submit/import bookkeeping and existing cancel-first orchestration."""
    store = InMemoryExecutionStore()
    deployment = _deployment()
    await store.create_deployment(deployment)
    parent_row = order_json(
        order_id="parent-venue",
        product_id="BTC-USD",
        product_type="SPOT",
        status="OPEN",
        filled_size="0",
    )
    parent_path = ORDERS + "/historical/parent-venue"
    initial = FakeTransport(
        gets={parent_path: [{"order": parent_row}]},
        posts={
            ORDERS: [
                {
                    "success": True,
                    "success_response": {
                        "order_id": "parent-venue",
                        **({"attached_order_id": "child-venue"} if learn_at_submit else {}),
                    },
                }
            ]
        },
    )
    parent = await submit_intent(
        store=store,
        broker=CoinbaseRestBroker(initial),
        deployment_id=deployment.id,
        product_id="BTC-USD",
        purpose=IntentPurpose.ENTRY,
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("1"),
        price=Decimal("100"),
        candle=_candle(),
        stop_trigger_price=Decimal("50"),
        take_profit_price=Decimal("200"),
    )
    assert parent.status is OrderStatus.OPEN
    assert len((await store.get_deployment(deployment.id)).orders) == (2 if learn_at_submit else 1)
    parent_row.update(client_order_id=parent.client_order_id, attached_order_id="child-venue")
    child_row = order_json(
        order_id="child-venue",
        product_id="BTC-USD",
        product_type="SPOT",
        client_order_id="venue-assigned-child",
        status="OPEN",
        filled_size="0",
    )
    child_path = ORDERS + "/historical/child-venue"
    cancel_path = ORDERS + "/batch_cancel"
    transport = FakeTransport(
        gets={
            parent_path: [{"order": parent_row}] * 3
            + [{"order": {**parent_row, "status": "CANCELLED"}}],
            child_path: [{"order": child_row}] * 3
            + [{"order": {**child_row, "status": "CANCELLED"}}],
            ORDERS + "/historical/fills": [{"fills": []}] * 2,
        },
        posts={
            cancel_path: [
                {"results": [{"order_id": venue_id, "success": True}]}
                for venue_id in (
                    ["parent-venue", "child-venue"] if cancel_first else ["child-venue"]
                )
            ]
        },
    )
    fresh = ProductRoutedBroker(CoinbaseRestBroker(transport), None)
    reconciled = await reconcile_open_orders(
        await store.get_deployment(deployment.id), broker=fresh, store=store
    )
    assert reconciled.deployment.status is DeploymentStatus.RUNNING
    child = next(order for order in reconciled.orders if order.parent_order_id == parent.id)
    assert child.client_order_id == f"{parent.client_order_id}:child"[:128]
    assert child.client_order_id != child_row["client_order_id"]
    assert child.status is OrderStatus.OPEN
    assert child.venue_observed_at is not None
    assert all(call[0] == "GET" for call in transport.calls)
    if cancel_first:
        await _cancel_open_orders(reconciled, broker=fresh, store=store)
    else:
        await _cancel_one_order(child, broker=fresh, store=store)
    persisted = await store.get_deployment(deployment.id)
    assert (
        next(order for order in persisted.orders if order.id == child.id).status
        is OrderStatus.CANCELED
    )
    assert [call[2] for call in transport.calls if call[0] == "POST"] == [
        {"order_ids": [venue_id]}
        for venue_id in (["parent-venue", "child-venue"] if cancel_first else ["child-venue"])
    ]
    assert initial.calls[0][2]["client_order_id"] == parent.client_order_id


@pytest.mark.anyio
@pytest.mark.parametrize("client_id", ["local-label", "x" * 128, "not-a-relationship:child"])
async def test_known_venue_id_is_routing_authority_not_local_label(client_id: str) -> None:
    """Routing reads exact venue id/product; it never recognizes a child suffix."""
    transport = FakeTransport(gets={ORDERS + "/historical/venue-test": [{"order": order_json()}]})
    identity = await CoinbaseRestBroker(transport).resolve_order_identity(
        venue_order_id="venue-test", client_order_id=client_id
    )
    assert identity is not None and identity.product_id == PRODUCT


@pytest.mark.anyio
@pytest.mark.parametrize("client_id", [CLIENT, CLIENT + ":child"])
async def test_client_only_lookup_does_not_infer_parent_or_child(client_id: str) -> None:
    """A submitted client identity must match exactly, irrespective of its suffix."""
    row = order_json(client_order_id="different-client")
    transport = FakeTransport(
        gets={ORDERS + "/historical/batch": [{"orders": [row], "has_next": False}]}
    )
    identity = await CoinbaseRestBroker(transport).resolve_order_identity(
        venue_order_id="", client_order_id=client_id
    )
    assert identity is None


@pytest.mark.anyio
@pytest.mark.parametrize("client_id", [CLIENT, CLIENT + ":child"])
async def test_futures_observation_retains_actual_client_match(client_id: str) -> None:
    """Routing authority does not relax a submitted futures parent's observation check."""
    row = order_json(client_order_id="different-client")
    path = ORDERS + "/historical/venue-test"
    transport = FakeTransport(gets={path: [{"order": row}] * 2})
    broker = ProductRoutedBroker(
        CoinbaseRestBroker(transport), CoinbaseFuturesBroker(transport, Sizes())
    )
    with pytest.raises(BrokerError, match="identity mismatch"):
        await broker.cancel_order(venue_order_id="venue-test", client_order_id=client_id)
    assert all(call[0] == "GET" for call in transport.calls)


@pytest.mark.anyio
async def test_client_lookup_checks_echo_again_after_matching_scan() -> None:
    """A correct list match cannot authorize a contradictory historical observation."""
    transport = FakeTransport(
        gets={
            ORDERS + "/historical/batch": [{"orders": [order_json()], "has_next": False}],
            ORDERS + "/historical/venue-test": [
                {"order": order_json(client_order_id="different-client")}
            ],
        }
    )
    broker = CoinbaseFuturesBroker(transport, Sizes())
    with pytest.raises(BrokerError, match="identity mismatch"):
        await broker.find_order_by_client_id(
            client_order_id=CLIENT,
            product_id=PRODUCT,
            submitted_at=datetime(2026, 1, 5, tzinfo=UTC),
        )
    assert all(call[0] == "GET" for call in transport.calls)
