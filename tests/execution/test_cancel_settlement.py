"""Canceled orders carry their first cancel time and settle after a consistent read (ADR 0141).

Regression: reconciliation re-read every canceled order every cycle forever, re-saving it with a
fresh ``updated_at`` that the cancel-rate breaker counted as a new cancellation.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from tests.execution.test_adr_0110_stopped_lifecycle import (
    _deployment,
    _order,
    _remote_fill,
    _Venue,
)
from thytrader.execution import reconcile as reconcile_module
from thytrader.execution.reconcile import CANCELED_SETTLE_DELAY, reconcile_open_orders
from thytrader.trading.ids import utc_now
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    DeploymentStatus,
    LifecycleCommand,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    with_cancel_time,
)

if TYPE_CHECKING:
    from uuid import UUID

pytestmark = pytest.mark.anyio


async def _canceled_stop(store: InMemoryExecutionStore) -> tuple[Order, UUID]:
    """One paused live book holding one sell stop the venue has canceled."""
    book = await _deployment(
        store,
        status=DeploymentStatus.PAUSED,
        command=LifecycleCommand.STOP_NEW_ENTRIES,
        detail="Operator paused entries.",
    )
    order = await _order(
        store,
        book.id,
        venue_order_id="stop-1",
        product_id="BTC-USD",
        kind=OrderKind.STOP_LIMIT,
        status=OrderStatus.CANCELED,
    )
    return order, book.id


async def _reconcile(store: InMemoryExecutionStore, book_id: UUID, venue: _Venue) -> Order:
    """Reconcile the book once and return its one stop as stored."""
    await reconcile_open_orders(
        await store.get_deployment(book_id), broker=venue, store=store, product_id="BTC-USD"
    )
    (stop,) = (await store.get_deployment(book_id)).orders
    return stop


def _reads(venue: _Venue) -> int:
    """How many order-state reads the venue served."""
    return sum(1 for kind, _venue_id in venue.events if kind == "get")


def _later(monkeypatch: pytest.MonkeyPatch, delta: timedelta) -> None:
    """Move reconciliation's clock forward by ``delta``."""
    now = utc_now() + delta
    monkeypatch.setattr(reconcile_module, "utc_now", lambda: now)


async def test_the_store_stamps_the_first_cancel_and_keeps_it() -> None:
    """The first CANCELED save sets canceled_at; later writes, UNKNOWN ones too, keep it."""
    store = InMemoryExecutionStore()
    order, _book = await _canceled_stop(store)
    (stored,) = store.orders.values()
    assert stored.canceled_at == order.updated_at
    later = order.updated_at + timedelta(minutes=5)
    await store.save_order(replace(stored, status=OrderStatus.UNKNOWN, updated_at=later))
    await store.save_order(
        replace(stored, status=OrderStatus.CANCELED, updated_at=later, canceled_at=later)
    )
    (again,) = store.orders.values()
    assert again.canceled_at == order.updated_at


def test_a_never_canceled_order_has_no_cancel_time() -> None:
    """Only a CANCELED order is stamped."""
    now = utc_now()
    open_order = Order(
        id=uuid4(),
        deployment_id=uuid4(),
        intent_id=uuid4(),
        client_order_id="client",
        side=OrderSide.SELL,
        kind=OrderKind.STOP_LIMIT,
        quantity=Decimal(1),
        status=OrderStatus.OPEN,
        created_at=now,
        updated_at=now,
    )
    assert with_cancel_time(open_order).canceled_at is None
    assert with_cancel_time(replace(open_order, status=OrderStatus.CANCELED)).canceled_at == now


async def test_a_recent_cancel_keeps_being_read() -> None:
    """Inside the settle delay the order stays watched, however consistent the read."""
    store = InMemoryExecutionStore()
    _order_row, book_id = await _canceled_stop(store)
    venue = _Venue(statuses={"stop-1": [OrderStatus.CANCELED]})
    first = await _reconcile(store, book_id, venue)
    second = await _reconcile(store, book_id, venue)
    assert first.settled_at is None
    assert second.settled_at is None
    assert _reads(venue) == 2


async def test_a_consistent_read_after_the_delay_settles_and_ends_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After the delay one consistent terminal read settles it; it is never read again."""
    store = InMemoryExecutionStore()
    _order_row, book_id = await _canceled_stop(store)
    venue = _Venue(statuses={"stop-1": [OrderStatus.CANCELED]})
    _later(monkeypatch, CANCELED_SETTLE_DELAY + timedelta(seconds=1))
    settled = await _reconcile(store, book_id, venue)
    assert settled.status is OrderStatus.CANCELED
    assert settled.settled_at is not None
    assert _reads(venue) == 1
    await _reconcile(store, book_id, venue)
    await _reconcile(store, book_id, venue)
    assert _reads(venue) == 1


async def test_a_failed_read_never_settles(monkeypatch: pytest.MonkeyPatch) -> None:
    """A read failure after the delay keeps the order UNKNOWN and watched."""
    store = InMemoryExecutionStore()
    order, book_id = await _canceled_stop(store)
    venue = _Venue(statuses={"stop-1": [OrderStatus.CANCELED]}, get_error={"stop-1"})
    _later(monkeypatch, CANCELED_SETTLE_DELAY + timedelta(seconds=1))
    failed = await _reconcile(store, book_id, venue)
    assert failed.status is OrderStatus.UNKNOWN
    assert failed.settled_at is None
    assert failed.canceled_at == order.updated_at
    venue.get_error.clear()
    healed = await _reconcile(store, book_id, venue)
    assert healed.status is OrderStatus.CANCELED
    assert healed.canceled_at == order.updated_at
    assert healed.settled_at is not None


async def test_unapplied_executed_quantity_never_settles(monkeypatch: pytest.MonkeyPatch) -> None:
    """A venue-reported fill without REST fills keeps the order watched and faulted."""
    store = InMemoryExecutionStore()
    order, book_id = await _canceled_stop(store)
    await store.save_order(replace(order, filled_quantity=Decimal("0.001")))
    venue = _Venue(statuses={"stop-1": [OrderStatus.CANCELED]})
    _later(monkeypatch, CANCELED_SETTLE_DELAY + timedelta(seconds=1))
    stored = await _reconcile(store, book_id, venue)
    assert stored.settled_at is None
    current = await store.get_deployment(book_id)
    assert current.deployment.mismatch_detail is not None


async def test_settlement_keeps_fill_progress_applied_by_the_same_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial fill published with a zero executed quantity is applied, then settled as is."""
    store = InMemoryExecutionStore()
    _order_row, book_id = await _canceled_stop(store)
    partial = replace(_remote_fill("stop-1"), venue_fill_id="late-part", quantity=Decimal("0.004"))
    venue = _Venue(statuses={"stop-1": [OrderStatus.CANCELED]}, fills={"stop-1": [(partial,)]})
    _later(monkeypatch, CANCELED_SETTLE_DELAY + timedelta(seconds=1))
    settled = await _reconcile(store, book_id, venue)
    assert settled.settled_at is not None
    assert settled.filled_quantity == Decimal("0.004")


async def test_a_backfilled_cancel_is_still_read_until_nearly_14_minutes_after_the_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Migration 0078 dates unknown cancels 61 s back; they stay watched through +13:58."""
    store = InMemoryExecutionStore()
    order, book_id = await _canceled_stop(store)
    upgrade = order.updated_at
    (stored,) = store.orders.values()
    store.orders[stored.id] = replace(stored, canceled_at=upgrade - timedelta(seconds=61))
    venue = _Venue(statuses={"stop-1": [OrderStatus.CANCELED]})
    now = upgrade + timedelta(minutes=13, seconds=58)
    monkeypatch.setattr(reconcile_module, "utc_now", lambda: now)
    watched = await _reconcile(store, book_id, venue)
    assert watched.settled_at is None
    assert _reads(venue) == 1
