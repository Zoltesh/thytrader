"""A latched read fault clears once every order reads back; paper orders are never re-read."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from tests.execution.test_reconcile import _FillsBroker, _LookupBroker
from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.execution.audit_scope import execution_audit_scope
from thytrader.execution.broker import BrokerError, SubmitResult
from thytrader.execution.paper import PaperBroker
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.trading.ids import uuid7
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentStatus,
    Fill,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    RuntimePhase,
)
from thytrader.trading.overlay import InstrumentScopedStore

_READ_FAILED = "Order reconciliation is unconfirmed: order read failed."
_FILL_READ_FAILED = "Order reconciliation is unconfirmed: fill read failed."
_WHEN = datetime(2026, 1, 5, tzinfo=UTC)


class _FailingBroker(_LookupBroker):
    """Every order read fails, as during a venue outage."""

    def __init__(self) -> None:
        """No configured result: reads always raise."""
        super().__init__(SubmitResult(status=OrderStatus.UNKNOWN, venue_order_id=""))

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Fail the read."""
        del venue_order_id, client_order_id
        raise BrokerError("simulated outage")


class _NoReadBroker(_LookupBroker):
    """Any venue read is a test failure."""

    def __init__(self) -> None:
        """No configured result."""
        super().__init__(SubmitResult(status=OrderStatus.OPEN, venue_order_id=""))

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Paper orders must not be read."""
        del venue_order_id, client_order_id
        raise AssertionError("a paper order with a venue id must not be read")


def _order(
    deployment_id: UUID,
    status: OrderStatus,
    *,
    canceled_at: datetime | None,
    settled_at: datetime | None,
) -> Order:
    """One sell exit on the book."""
    return Order(
        id=uuid7(_WHEN),
        deployment_id=deployment_id,
        intent_id=uuid7(_WHEN),
        client_order_id="exit-1",
        side=OrderSide.SELL,
        kind=OrderKind.STOP_LIMIT,
        quantity=Decimal("1"),
        status=status,
        created_at=_WHEN,
        updated_at=_WHEN,
        price=Decimal("90"),
        venue_order_id="venue-exit-1",
        product_id="BTC-USD",
        canceled_at=canceled_at,
        settled_at=settled_at,
    )


async def _book(
    *,
    status: DeploymentStatus = DeploymentStatus.PAUSED,
    detail: str | None = _READ_FAILED,
    mode: DeploymentMode = DeploymentMode.LIVE,
    order_status: OrderStatus = OrderStatus.CANCELED,
    canceled_at: datetime | None = None,
    settled_at: datetime | None = None,
) -> tuple[InMemoryExecutionStore, UUID]:
    """A flat book holding one exit order and a latched fault detail."""
    store = InMemoryExecutionStore()
    deployment = Deployment(
        id=uuid7(_WHEN),
        strategy_fingerprint="sha256:" + ("a" * 64),
        strategy_id=UUID(int=1),
        product_id="BTC-USD",
        mode=mode,
        status=status,
        cash=Decimal("1000"),
        phase=RuntimePhase.FLAT,
        created_at=_WHEN,
        updated_at=_WHEN,
        mismatch_detail=detail,
    )
    await store.create_deployment(deployment)
    await store.save_order(
        _order(deployment.id, order_status, canceled_at=canceled_at, settled_at=settled_at)
    )
    return store, deployment.id


async def _reconcile(
    store: InMemoryExecutionStore, deployment_id: UUID, broker: _LookupBroker | PaperBroker
) -> tuple[Deployment, tuple[Order, ...], list[str]]:
    """One reconcile walk under a captured audit trail."""
    audit = InMemoryAuditEventStore()
    with execution_audit_scope(audit):
        result = await reconcile_open_orders(
            await store.get_deployment(deployment_id),
            broker=broker,
            store=store,
            product_id="BTC-USD",
        )
    actions = [event.action for event in await audit.list_recent(limit=50)]
    return result.deployment, result.orders, actions


@pytest.mark.anyio
@pytest.mark.parametrize("status", [DeploymentStatus.PAUSED, DeploymentStatus.STOPPED])
@pytest.mark.parametrize("detail", [_READ_FAILED, _FILL_READ_FAILED])
async def test_a_latched_read_fault_on_a_settled_book_clears_without_resuming(
    status: DeploymentStatus, detail: str
) -> None:
    """Terminal, settled orders disprove an old read failure; the status never changes."""
    store, deployment_id = await _book(
        status=status, detail=detail, canceled_at=_WHEN, settled_at=_WHEN
    )
    deployment, _orders, actions = await _reconcile(store, deployment_id, _NoReadBroker())
    assert deployment.mismatch_detail is None
    assert deployment.status is status
    assert "reconcile_read_fault_cleared" in actions


@pytest.mark.anyio
async def test_a_later_successful_read_clears_the_latch() -> None:
    """An order left UNKNOWN by the failed read reads back canceled: the fault is disproved."""
    store, deployment_id = await _book(order_status=OrderStatus.UNKNOWN)
    broker = _LookupBroker(SubmitResult(status=OrderStatus.CANCELED, venue_order_id="venue-exit-1"))
    deployment, orders, actions = await _reconcile(store, deployment_id, broker)
    assert orders[0].status is OrderStatus.CANCELED
    assert deployment.mismatch_detail is None
    assert deployment.status is DeploymentStatus.PAUSED
    assert "reconcile_read_fault_cleared" in actions


@pytest.mark.anyio
async def test_a_repeated_read_failure_keeps_the_fault() -> None:
    """The same failure again leaves the detail unchanged, so it must not count as success."""
    store, deployment_id = await _book(order_status=OrderStatus.UNKNOWN)
    deployment, orders, actions = await _reconcile(store, deployment_id, _FailingBroker())
    assert orders[0].status is OrderStatus.UNKNOWN
    assert deployment.mismatch_detail == _READ_FAILED
    assert deployment.status is DeploymentStatus.PAUSED
    assert "reconcile_read_fault_cleared" not in actions


@pytest.mark.anyio
async def test_a_venue_unknown_answer_keeps_the_fault() -> None:
    """A read that answers UNKNOWN is not evidence."""
    store, deployment_id = await _book(order_status=OrderStatus.UNKNOWN)
    broker = _LookupBroker(SubmitResult(status=OrderStatus.UNKNOWN, venue_order_id="venue-exit-1"))
    deployment, _orders, actions = await _reconcile(store, deployment_id, broker)
    assert deployment.mismatch_detail is not None
    assert "reconcile_read_fault_cleared" not in actions


@pytest.mark.anyio
async def test_a_repeated_venue_unknown_fault_is_never_cleared_by_its_fills() -> None:
    """Fills can mark the order filled while the venue still answers UNKNOWN; keep the fault."""
    unknown = "Order reconciliation is unconfirmed: venue status is unknown."
    store, deployment_id = await _book(
        detail=unknown, order_status=OrderStatus.UNKNOWN, mode=DeploymentMode.LIVE
    )
    order = (await store.get_deployment(deployment_id)).orders[0]
    fill = Fill(
        id=uuid7(_WHEN),
        deployment_id=deployment_id,
        order_id=order.id,
        venue_fill_id="vf-1",
        price=Decimal("90"),
        quantity=Decimal("1"),
        fee=Decimal("0.1"),
        filled_at=_WHEN,
        venue_order_id="venue-exit-1",
    )
    broker = _FillsBroker(
        SubmitResult(status=OrderStatus.UNKNOWN, venue_order_id="venue-exit-1"), (fill,)
    )
    deployment, _orders, actions = await _reconcile(store, deployment_id, broker)
    assert deployment.mismatch_detail == unknown
    assert "reconcile_read_fault_cleared" not in actions


@pytest.mark.anyio
async def test_a_one_product_walk_never_clears_a_fault_another_product_still_holds() -> None:
    """A multi-instrument walk through one product's view checks every product's orders."""
    store, deployment_id = await _book(order_status=OrderStatus.UNKNOWN)
    eth_order = _order(deployment_id, OrderStatus.UNKNOWN, canceled_at=None, settled_at=None)
    await store.save_order(
        replace(
            eth_order,
            id=uuid7(_WHEN + timedelta(seconds=1)),
            client_order_id="exit-eth",
            venue_order_id="venue-exit-eth",
            product_id="ETH-USD",
        )
    )
    broker = _LookupBroker(
        SubmitResult(status=OrderStatus.CANCELED, venue_order_id="venue-exit-eth")
    )
    audit = InMemoryAuditEventStore()
    with execution_audit_scope(audit):
        scoped = InstrumentScopedStore(store, "ETH-USD")
        await reconcile_open_orders(
            await scoped.get_deployment(deployment_id),
            broker=broker,
            store=scoped,
            product_id="ETH-USD",
        )
    full = await store.get_deployment(deployment_id)
    assert {order.product_id: order.status for order in full.orders} == {
        "BTC-USD": OrderStatus.UNKNOWN,
        "ETH-USD": OrderStatus.CANCELED,
    }
    assert full.deployment.mismatch_detail == _READ_FAILED
    actions = [event.action for event in await audit.list_recent(limit=50)]
    assert "reconcile_read_fault_cleared" not in actions


@pytest.mark.anyio
@pytest.mark.parametrize(
    "detail", ["Filled order has no REST fills.", "Operator paused entries.", None]
)
async def test_other_details_are_never_cleared(detail: str | None) -> None:
    """Only read-failure details are disprovable by a clean walk."""
    store, deployment_id = await _book(detail=detail, canceled_at=_WHEN, settled_at=_WHEN)
    deployment, _orders, actions = await _reconcile(store, deployment_id, _NoReadBroker())
    assert deployment.mismatch_detail == detail
    assert "reconcile_read_fault_cleared" not in actions


@pytest.mark.anyio
async def test_a_canceled_paper_exit_is_not_revived_by_reconcile() -> None:
    """The paper broker answers every lookup OPEN; an order it holds is never read back."""
    store, deployment_id = await _book(
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        detail=None,
        canceled_at=_WHEN - timedelta(hours=1),
    )
    deployment, orders, _actions = await _reconcile(store, deployment_id, PaperBroker())
    assert orders[0].status is OrderStatus.CANCELED
    assert deployment.status is DeploymentStatus.RUNNING
    assert deployment.mismatch_detail is None


@pytest.mark.anyio
async def test_a_paper_book_on_a_venue_broker_is_still_read() -> None:
    """The skip keys on the paper broker, not the book's mode: a replayed or fake venue reads."""
    store, deployment_id = await _book(
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        detail=None,
        order_status=OrderStatus.UNKNOWN,
    )
    broker = _LookupBroker(SubmitResult(status=OrderStatus.CANCELED, venue_order_id="venue-exit-1"))
    _deployment, orders, _actions = await _reconcile(store, deployment_id, broker)
    assert orders[0].status is OrderStatus.CANCELED


@pytest.mark.anyio
async def test_a_filled_paper_order_without_its_fill_still_faults() -> None:
    """An interrupted paper submit (order saved filled, fill never written) pauses fail-closed."""
    store, deployment_id = await _book(
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        detail=None,
        order_status=OrderStatus.FILLED,
    )
    order = (await store.get_deployment(deployment_id)).orders[0]
    await store.save_order(replace(order, filled_quantity=order.quantity))
    deployment, _orders, _actions = await _reconcile(store, deployment_id, PaperBroker())
    assert deployment.mismatch_detail == "Filled order has no REST fills."
    assert deployment.status is DeploymentStatus.PAUSED


@pytest.mark.anyio
async def test_a_sibling_execution_without_fills_keeps_the_fault() -> None:
    """A cancelled sibling that reported an executed quantity but has no fills holds the fault."""
    store, deployment_id = await _book(detail=_FILL_READ_FAILED)
    btc = (await store.get_deployment(deployment_id)).orders[0]
    await store.save_order(replace(btc, filled_quantity=Decimal("0.5")))
    eth = _order(deployment_id, OrderStatus.UNKNOWN, canceled_at=None, settled_at=None)
    await store.save_order(
        replace(
            eth,
            id=uuid7(_WHEN + timedelta(seconds=1)),
            client_order_id="exit-eth",
            venue_order_id="venue-exit-eth",
            product_id="ETH-USD",
        )
    )
    broker = _LookupBroker(
        SubmitResult(status=OrderStatus.CANCELED, venue_order_id="venue-exit-eth")
    )
    audit = InMemoryAuditEventStore()
    with execution_audit_scope(audit):
        scoped = InstrumentScopedStore(store, "ETH-USD")
        await reconcile_open_orders(
            await scoped.get_deployment(deployment_id),
            broker=broker,
            store=scoped,
            product_id="ETH-USD",
        )
    full = await store.get_deployment(deployment_id)
    assert full.deployment.mismatch_detail == _FILL_READ_FAILED
    actions = [event.action for event in await audit.list_recent(limit=50)]
    assert "reconcile_read_fault_cleared" not in actions
