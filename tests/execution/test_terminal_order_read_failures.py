"""A failed venue read never turns a terminal order back into a possibly working one.

Regression: a paused live book kept re-reading its canceled trailing stops; one failed fill
read saved such a stop as ``UNKNOWN``, and supervision then raised a critical
``STOP_TRIGGERED_UNFILLED`` for a stop that had been canceled days earlier.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from tests.execution.test_adr_0110_stopped_lifecycle import _deployment, _order, _Venue
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    DeploymentStatus,
    LifecycleCommand,
    OrderKind,
    OrderStatus,
)

pytestmark = pytest.mark.anyio


async def _reconcile_one(
    *, status: OrderStatus, venue: _Venue, filled: Decimal | None = None
) -> tuple[OrderStatus, str]:
    """Reconcile a paused live book holding one stop; return its stored status and fault."""
    store = InMemoryExecutionStore()
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
        await store.save_order(replace(order, filled_quantity=filled))
    result = await reconcile_open_orders(
        await store.get_deployment(book.id), broker=venue, store=store, product_id="BTC-USD"
    )
    stored = next(item for item in result.orders if item.id == order.id)
    return stored.status, result.deployment.mismatch_detail or ""


async def test_failed_fill_read_keeps_a_canceled_stop_canceled() -> None:
    """The venue says CANCELED and the fill page fails: the stop stays canceled, still faulted."""
    venue = _Venue(statuses={"stop-1": [OrderStatus.CANCELED]}, fills_error={"stop-1"})
    status, detail = await _reconcile_one(status=OrderStatus.CANCELED, venue=venue)
    assert status is OrderStatus.CANCELED
    assert "fill read failed" in detail


async def test_failed_order_read_keeps_a_canceled_stop_canceled() -> None:
    """The order read itself fails: a known-canceled stop is not reopened as UNKNOWN."""
    venue = _Venue(get_error={"stop-1"})
    status, detail = await _reconcile_one(status=OrderStatus.CANCELED, venue=venue)
    assert status is OrderStatus.CANCELED
    assert "order read failed" in detail


async def test_failed_order_read_still_marks_a_working_stop_unknown() -> None:
    """An OPEN stop may still be working, so a failed read keeps the fail-closed UNKNOWN."""
    venue = _Venue(get_error={"stop-1"})
    status, detail = await _reconcile_one(status=OrderStatus.OPEN, venue=venue)
    assert status is OrderStatus.UNKNOWN
    assert "order read failed" in detail


async def test_failed_read_marks_a_partially_filled_order_unknown() -> None:
    """A locally FILLED order short of its quantity may still work at the venue."""
    venue = _Venue(get_error={"stop-1"})
    status, _ = await _reconcile_one(
        status=OrderStatus.FILLED, venue=venue, filled=Decimal("0.0005")
    )
    assert status is OrderStatus.UNKNOWN


async def test_failed_read_keeps_a_fully_filled_order_filled() -> None:
    """A FILLED order for its full quantity is terminal and stays FILLED."""
    store = InMemoryExecutionStore()
    venue = _Venue(get_error={"stop-1"})
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
        status=OrderStatus.FILLED,
    )
    await store.save_order(replace(order, filled_quantity=order.quantity))
    result = await reconcile_open_orders(
        await store.get_deployment(book.id), broker=venue, store=store, product_id="BTC-USD"
    )
    stored = next(item for item in result.orders if item.id == order.id)
    assert stored.status is OrderStatus.FILLED
