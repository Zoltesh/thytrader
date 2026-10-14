"""A failed venue read keeps orders UNKNOWN but marks ones the venue already closed.

Regression: a paused live book kept re-reading its canceled trailing stops; one failed read
saved such a stop as a plain ``UNKNOWN``, and supervision then raised a critical
``STOP_TRIGGERED_UNFILLED`` for a stop canceled days earlier. Every order still becomes
``UNKNOWN`` so exits, flatten settlement and exposure stay fail-closed while late fills are
unknown; terminal ones carry ``TERMINAL_READ_UNCONFIRMED_REASON``.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from tests.execution.test_adr_0110_stopped_lifecycle import (
    _deployment,
    _order,
    _remote_fill,
    _supervise,
    _Venue,
)
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    TERMINAL_READ_UNCONFIRMED_REASON,
    DeploymentStatus,
    IntentPurpose,
    LifecycleCommand,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
)

if TYPE_CHECKING:
    from uuid import UUID

pytestmark = pytest.mark.anyio


async def _paused_book_with_stop(
    store: InMemoryExecutionStore, *, status: OrderStatus, filled: Decimal | None = None
) -> tuple[Order, UUID]:
    """One paused live book holding one sell stop in ``status``."""
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
        status=status,
    )
    if filled is not None:
        order = replace(order, filled_quantity=filled)
        await store.save_order(order)
    return order, book.id


async def _reconcile_once(
    store: InMemoryExecutionStore, book_id: UUID, venue: _Venue, order: Order
) -> tuple[Order, str]:
    """Reconcile once; return the stored order and the book's fault detail."""
    result = await reconcile_open_orders(
        await store.get_deployment(book_id),
        broker=venue,
        store=store,
        product_id="BTC-USD",
    )
    stored = next(item for item in result.orders if item.id == order.id)
    return stored, result.deployment.mismatch_detail or ""


@pytest.mark.parametrize(
    ("venue", "detail"),
    [
        (_Venue(statuses={"stop-1": [OrderStatus.CANCELED]}, fills_error={"stop-1"}), "fill read"),
        (_Venue(get_error={"stop-1"}), "order read"),
    ],
)
async def test_failed_read_of_a_canceled_stop_is_unknown_and_marked_terminal(
    venue: _Venue, detail: str
) -> None:
    """The order stays fail-closed UNKNOWN, faulted, and marked as already closed at the venue."""
    store = InMemoryExecutionStore()
    order, book_id = await _paused_book_with_stop(store, status=OrderStatus.CANCELED)
    stored, fault = await _reconcile_once(store, book_id, venue, order)
    assert stored.status is OrderStatus.UNKNOWN
    assert stored.reject_reason == TERMINAL_READ_UNCONFIRMED_REASON
    assert stored.venue_observed_at is None
    assert f"{detail} failed" in fault


async def test_the_marker_survives_repeated_failures_and_clears_on_a_good_read() -> None:
    """A second failure keeps the marker; the next successful read restores venue truth."""
    store = InMemoryExecutionStore()
    order, book_id = await _paused_book_with_stop(store, status=OrderStatus.CANCELED)
    venue = _Venue(statuses={"stop-1": [OrderStatus.CANCELED]}, get_error={"stop-1"})
    await _reconcile_once(store, book_id, venue, order)
    again, _ = await _reconcile_once(store, book_id, venue, order)
    assert again.reject_reason == TERMINAL_READ_UNCONFIRMED_REASON
    venue.get_error.clear()
    healed, _ = await _reconcile_once(store, book_id, venue, order)
    assert healed.status is OrderStatus.CANCELED
    assert healed.reject_reason is None


@pytest.mark.parametrize(
    ("status", "fully_filled", "partial", "marked"),
    [
        (OrderStatus.OPEN, False, None, False),
        (OrderStatus.FILLED, False, Decimal("0.0005"), False),
        (OrderStatus.FILLED, True, None, True),
    ],
)
async def test_only_venue_closed_orders_are_marked(
    status: OrderStatus, fully_filled: bool, partial: Decimal | None, marked: bool
) -> None:
    """Working and partially filled orders stay plain UNKNOWN; fully filled ones are marked."""
    store = InMemoryExecutionStore()
    order, book_id = await _paused_book_with_stop(store, status=status, filled=partial)
    if fully_filled:
        order = replace(order, filled_quantity=order.quantity)
        await store.save_order(order)
    stored, fault = await _reconcile_once(store, book_id, _Venue(get_error={"stop-1"}), order)
    assert stored.status is OrderStatus.UNKNOWN
    assert (stored.reject_reason == TERMINAL_READ_UNCONFIRMED_REASON) is marked
    assert "order read failed" in fault


async def test_flatten_never_settles_while_a_canceled_entry_read_fails() -> None:
    """A stopped flatten keeps the fault until a read succeeds, then ingests the late fill."""
    store = InMemoryExecutionStore()
    deployment = await _deployment(store)
    entry = await _order(
        store,
        deployment.id,
        venue_order_id="late-entry",
        product_id="BTC-USD",
        kind=OrderKind.POST_ONLY_LIMIT,
        side=OrderSide.BUY,
        purpose=IntentPurpose.ENTRY,
        status=OrderStatus.CANCELED,
    )
    venue = _Venue(statuses={"late-entry": [OrderStatus.CANCELED]}, get_error={"late-entry"})
    await _supervise(store, deployment, venue)
    current = await store.get_deployment(deployment.id)
    stored = next(item for item in current.orders if item.id == entry.id)
    assert stored.status is OrderStatus.UNKNOWN
    assert "order read failed" in (current.deployment.mismatch_detail or "")
    assert current.fills == ()
    venue.get_error.clear()
    venue.fills["late-entry"] = [(_remote_fill("late-entry"),)]
    await _supervise(store, deployment, venue)
    current = await store.get_deployment(deployment.id)
    healed = next(item for item in current.orders if item.id == entry.id)
    assert healed.status is not OrderStatus.UNKNOWN
    assert healed.reject_reason is None
    assert [fill.venue_order_id for fill in current.fills] == ["late-entry"]
