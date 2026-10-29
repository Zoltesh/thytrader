"""Fills project in execution order, and an over-closing fill pauses instead of erasing inventory.

PostgreSQL loads orders and fills newest first. When a parent entry fill and its attached
child's exit fill are both unimported in one reconcile pass (for example after the worker
was offline), walking that order projected the child's exit first as a phantom position,
and the parent's larger fill then over-closed it and silently dropped the residual.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from tests.execution.test_reconcile import _LookupBroker
from thytrader.execution.broker import BrokerError, SubmitResult
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.trading.fill_ledger import OVER_CLOSING_FILL_DETAIL, ingest_fill
from thytrader.trading.ids import utc_now, uuid7
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Fill,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    PositionSide,
    RuntimePhase,
)

pytestmark = pytest.mark.anyio


class _NewestFirstStore(InMemoryExecutionStore):
    """In-memory store that returns orders and fills newest first, as PostgreSQL does."""

    async def get_deployment(self, deployment_id: UUID) -> DeploymentSnapshot:
        snapshot = await super().get_deployment(deployment_id)
        return replace(
            snapshot,
            orders=tuple(
                sorted(
                    snapshot.orders, key=lambda order: (order.created_at, order.id), reverse=True
                )
            ),
            fills=tuple(
                sorted(snapshot.fills, key=lambda fill: (fill.filled_at, fill.id), reverse=True)
            ),
        )


class _Venue(_LookupBroker):
    """A filled parent entry and a partly filled attached child, each with its own fills."""

    def __init__(self, fills: dict[str, tuple[Fill, ...]], states: dict[str, SubmitResult]) -> None:
        super().__init__(next(iter(states.values())))
        self._fills = fills
        self._states = states

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        del client_order_id
        return self._states[venue_order_id]

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        del product_id
        return self._fills.get(order_id or "", ())


async def _pending_live_book(store: InMemoryExecutionStore) -> tuple[Deployment, Order, Order]:
    now = utc_now()
    deployment = Deployment(
        id=uuid7(now),
        strategy_fingerprint="sha256:" + ("a" * 64),
        strategy_id=UUID(int=1),
        product_id="BTC-USD",
        mode=DeploymentMode.LIVE,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("10000"),
        phase=RuntimePhase.PENDING_ENTRY,
        pending_stop_price=Decimal("90"),
        pending_target_price=Decimal("120"),
        timeframe="1h",
        created_at=now,
        updated_at=now,
    )
    await store.create_deployment(deployment)
    parent = Order(
        id=uuid7(now),
        deployment_id=deployment.id,
        intent_id=uuid7(now),
        client_order_id="entry",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("1"),
        price=Decimal("100"),
        status=OrderStatus.OPEN,
        created_at=now,
        updated_at=now,
        venue_order_id="venue-entry",
        product_id="BTC-USD",
    )
    # The attached child is stamped with its parent's creation time.
    child = replace(
        parent,
        id=uuid7(now + timedelta(milliseconds=5)),
        client_order_id="entry:child",
        side=OrderSide.SELL,
        kind=OrderKind.TRIGGER_BRACKET,
        price=Decimal("120"),
        stop_trigger_price=Decimal("90"),
        take_profit_price=Decimal("120"),
        venue_order_id="venue-child",
        parent_order_id=parent.id,
    )
    await store.save_order(parent)
    await store.save_order(child)
    return deployment, parent, child


def _remote(order: Order, *, name: str, quantity: str, price: str, minutes: int) -> Fill:
    return Fill(
        id=uuid7(utc_now()),
        deployment_id=order.deployment_id,
        order_id=order.id,
        venue_fill_id=name,
        price=Decimal(price),
        quantity=Decimal(quantity),
        fee=Decimal("0"),
        filled_at=order.created_at + timedelta(minutes=minutes),
        venue_order_id=order.venue_order_id,
    )


async def test_parent_fill_projects_before_its_childs_exit() -> None:
    """L3: entry 1 then a 0.3 take-profit leaves a 0.7 long, not a dropped residual."""
    store = _NewestFirstStore()
    deployment, parent, child = await _pending_live_book(store)
    venue = _Venue(
        fills={
            "venue-entry": (_remote(parent, name="f-entry", quantity="1", price="100", minutes=1),),
            "venue-child": (_remote(child, name="f-tp", quantity="0.3", price="120", minutes=30),),
        },
        states={
            "venue-entry": SubmitResult(
                status=OrderStatus.FILLED,
                venue_order_id="venue-entry",
                filled_quantity=Decimal("1"),
                attached_child_venue_order_id="venue-child",
            ),
            "venue-child": SubmitResult(
                status=OrderStatus.OPEN,
                venue_order_id="venue-child",
                filled_quantity=Decimal("0.3"),
            ),
        },
    )

    result = await reconcile_open_orders(
        await store.get_deployment(deployment.id), broker=venue, store=store
    )

    assert result.position is not None
    assert result.position.side is PositionSide.LONG
    assert result.position.quantity == Decimal("0.7")
    assert result.deployment.cash == Decimal("10000") - Decimal("100") + Decimal("36")
    assert result.deployment.mismatch_detail is None


async def test_unapplied_fills_replay_in_execution_order() -> None:
    """L3: stored-but-unapplied evidence replays oldest first, whatever order the store loads."""
    store = _NewestFirstStore()
    deployment, parent, child = await _pending_live_book(store)
    await store.save_fill(_remote(parent, name="f-entry", quantity="1", price="100", minutes=1))
    await store.save_fill(_remote(child, name="f-tp", quantity="0.3", price="120", minutes=30))
    venue = _Venue(
        fills={},
        states={
            "venue-entry": SubmitResult(
                status=OrderStatus.FILLED,
                venue_order_id="venue-entry",
                filled_quantity=Decimal("1"),
                attached_child_venue_order_id="venue-child",
            ),
            "venue-child": SubmitResult(
                status=OrderStatus.OPEN,
                venue_order_id="venue-child",
                filled_quantity=Decimal("0.3"),
            ),
        },
    )

    result = await reconcile_open_orders(
        await store.get_deployment(deployment.id), broker=venue, store=store
    )

    assert result.position is not None
    assert result.position.quantity == Decimal("0.7")
    assert result.position.side is PositionSide.LONG


async def test_an_over_closing_fill_pauses_with_the_residual_named() -> None:
    """L3: a fill larger than the position flattens the book and pauses it; cash stays exact."""
    store = InMemoryExecutionStore()
    deployment, parent, child = await _pending_live_book(store)
    snapshot = await store.get_deployment(deployment.id)
    opened = await ingest_fill(
        snapshot,
        fill=_remote(parent, name="f-entry", quantity="1", price="100", minutes=1),
        order=parent,
        store=store,
    )
    assert opened.snapshot.position is not None

    closed = await ingest_fill(
        opened.snapshot,
        fill=_remote(child, name="f-over", quantity="1.5", price="120", minutes=30),
        order=child,
        store=store,
    )

    assert closed.snapshot.position is None
    assert closed.snapshot.deployment.status is DeploymentStatus.PAUSED
    assert closed.snapshot.deployment.mismatch_detail == OVER_CLOSING_FILL_DETAIL
    assert closed.snapshot.deployment.cash == Decimal("10000") - Decimal("100") + Decimal("180")


class _FlakyVenue(_Venue):
    """A venue whose fill listing fails for some orders."""

    def __init__(
        self,
        fills: dict[str, tuple[Fill, ...]],
        states: dict[str, SubmitResult],
        *,
        unreadable: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__(fills, states)
        self.unreadable = unreadable
        self.listings: list[str] = []

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        self.listings.append(order_id or "")
        if order_id in self.unreadable:
            raise BrokerError("transient")
        return await super().list_fills(product_id=product_id, order_id=order_id)


def _filled(venue_order_id: str, quantity: str) -> SubmitResult:
    return SubmitResult(
        status=OrderStatus.FILLED, venue_order_id=venue_order_id, filled_quantity=Decimal(quantity)
    )


async def _entered(store: InMemoryExecutionStore) -> tuple[Deployment, Order, Order]:
    """A live long of 1 whose entry filled, with its attached take-profit still resting."""
    deployment, parent, child = await _pending_live_book(store)
    entry_fill = _remote(parent, name="f-entry", quantity="1", price="100", minutes=1)
    venue = _Venue(
        fills={"venue-entry": (entry_fill,)},
        states={
            "venue-entry": replace(
                _filled("venue-entry", "1"), attached_child_venue_order_id="venue-child"
            ),
            "venue-child": SubmitResult(status=OrderStatus.OPEN, venue_order_id="venue-child"),
        },
    )
    entered = await reconcile_open_orders(
        await store.get_deployment(deployment.id), broker=venue, store=store
    )
    assert entered.position is not None
    assert entered.position.quantity == Decimal("1")
    return deployment, parent, child


async def test_a_newer_order_that_filled_first_is_applied_first() -> None:
    """A pyramid add created after the bracket but filled before it keeps the book long."""
    store = _NewestFirstStore()
    deployment, parent, child = await _entered(store)
    add = replace(
        parent,
        id=uuid7(parent.created_at + timedelta(hours=5)),
        client_order_id="add",
        venue_order_id="venue-add",
        created_at=parent.created_at + timedelta(hours=5),
        pyramid_add=True,
    )
    await store.save_order(add)
    venue = _Venue(
        fills={
            "venue-add": (_remote(add, name="f-add", quantity="1", price="110", minutes=60),),
            "venue-child": (
                _remote(child, name="f-tp", quantity="1", price="120", minutes=7 * 60),
            ),
        },
        states={
            "venue-entry": replace(
                _filled("venue-entry", "1"), attached_child_venue_order_id="venue-child"
            ),
            "venue-add": _filled("venue-add", "1"),
            "venue-child": _filled("venue-child", "1"),
        },
    )

    result = await reconcile_open_orders(
        await store.get_deployment(deployment.id), broker=venue, store=store
    )

    assert result.position is not None
    assert result.position.side is PositionSide.LONG
    assert result.position.quantity == Decimal("1")
    assert result.deployment.mismatch_detail is None
    assert result.deployment.status is DeploymentStatus.RUNNING


async def test_a_childs_fills_wait_for_an_unread_parent() -> None:
    """A parent whose fills cannot be listed holds its child's exit: no phantom short."""
    store = _NewestFirstStore()
    deployment, parent, child = await _pending_live_book(store)
    venue = _FlakyVenue(
        fills={
            "venue-entry": (_remote(parent, name="f-entry", quantity="1", price="100", minutes=1),),
            "venue-child": (_remote(child, name="f-tp", quantity="0.3", price="120", minutes=30),),
        },
        states={
            "venue-entry": replace(
                _filled("venue-entry", "1"), attached_child_venue_order_id="venue-child"
            ),
            "venue-child": SubmitResult(
                status=OrderStatus.OPEN,
                venue_order_id="venue-child",
                filled_quantity=Decimal("0.3"),
            ),
        },
        unreadable=frozenset({"venue-entry"}),
    )

    held = await reconcile_open_orders(
        await store.get_deployment(deployment.id), broker=venue, store=store
    )

    assert held.position is None
    assert held.fills == ()
    assert held.deployment.status is DeploymentStatus.PAUSED
    venue.unreadable = frozenset()
    recovered = await reconcile_open_orders(held, broker=venue, store=store)
    assert recovered.position is not None
    assert recovered.position.side is PositionSide.LONG
    assert recovered.position.quantity == Decimal("0.7")


async def test_each_order_is_listed_once_per_pass() -> None:
    """The walk reuses the execution-order listing instead of asking the venue again."""
    store = _NewestFirstStore()
    deployment, parent, _child = await _pending_live_book(store)
    venue = _FlakyVenue(
        fills={
            "venue-entry": (_remote(parent, name="f-entry", quantity="1", price="100", minutes=1),)
        },
        states={
            "venue-entry": replace(
                _filled("venue-entry", "1"), attached_child_venue_order_id="venue-child"
            ),
            "venue-child": SubmitResult(status=OrderStatus.OPEN, venue_order_id="venue-child"),
        },
    )

    await reconcile_open_orders(
        await store.get_deployment(deployment.id), broker=venue, store=store
    )

    assert sorted(venue.listings) == ["venue-child", "venue-entry"]


class _LaggingVenue(_FlakyVenue):
    """A venue whose listing for some orders is empty until it has been asked a few times."""

    def __init__(
        self,
        fills: dict[str, tuple[Fill, ...]],
        states: dict[str, SubmitResult],
        *,
        empty_reads: dict[str, int],
    ) -> None:
        super().__init__(fills, states)
        self.empty_reads = dict(empty_reads)

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        remaining = self.empty_reads.get(order_id or "", 0)
        if remaining > 0:
            self.empty_reads[order_id or ""] = remaining - 1
            self.listings.append(order_id or "")
            return ()
        return await super().list_fills(product_id=product_id, order_id=order_id)


def _bracketed_states(child_filled: str) -> dict[str, SubmitResult]:
    return {
        "venue-entry": replace(
            _filled("venue-entry", "1"), attached_child_venue_order_id="venue-child"
        ),
        "venue-child": SubmitResult(
            status=OrderStatus.OPEN,
            venue_order_id="venue-child",
            filled_quantity=Decimal(child_filled),
        ),
    }


async def test_an_empty_parent_listing_holds_the_childs_exit() -> None:
    """A parent listing that is still empty while the child sold holds the child: no phantom."""
    store = _NewestFirstStore()
    deployment, parent, child = await _pending_live_book(store)
    venue = _LaggingVenue(
        fills={
            "venue-entry": (_remote(parent, name="f-entry", quantity="1", price="100", minutes=1),),
            "venue-child": (_remote(child, name="f-tp", quantity="0.3", price="120", minutes=30),),
        },
        states=_bracketed_states("0.3"),
        empty_reads={"venue-entry": 2},
    )

    held = await reconcile_open_orders(
        await store.get_deployment(deployment.id), broker=venue, store=store
    )

    assert held.position is None
    assert all(fill.order_id != child.id for fill in held.fills)
    recovered = await reconcile_open_orders(held, broker=venue, store=store)
    assert recovered.position is not None
    assert recovered.position.side is PositionSide.LONG
    assert recovered.position.quantity == Decimal("0.7")


async def test_a_read_reporting_new_executions_relists_within_the_pass() -> None:
    """A parent whose read reports a fill the earlier listing lacked is re-listed and applied."""
    store = _NewestFirstStore()
    deployment, parent, child = await _pending_live_book(store)
    venue = _LaggingVenue(
        fills={
            "venue-entry": (_remote(parent, name="f-entry", quantity="1", price="100", minutes=1),),
            "venue-child": (_remote(child, name="f-tp", quantity="0.3", price="120", minutes=30),),
        },
        states=_bracketed_states("0.3"),
        empty_reads={"venue-entry": 1},
    )

    result = await reconcile_open_orders(
        await store.get_deployment(deployment.id), broker=venue, store=store
    )

    assert result.position is not None
    assert result.position.quantity == Decimal("0.7")
    assert result.deployment.mismatch_detail is None


async def test_a_child_found_by_the_read_waits_for_its_unread_parent() -> None:
    """A child first revealed by its parent's read is held while the parent's fills are unread."""
    store = _NewestFirstStore()
    deployment, parent, child = await _pending_live_book(store)
    store.orders.pop(child.id)
    venue = _FlakyVenue(
        fills={
            "venue-entry": (_remote(parent, name="f-entry", quantity="1", price="100", minutes=1),),
            "venue-child": (_remote(child, name="f-tp", quantity="0.3", price="120", minutes=30),),
        },
        states=_bracketed_states("0.3"),
        unreadable=frozenset({"venue-entry"}),
    )

    held = await reconcile_open_orders(
        await store.get_deployment(deployment.id), broker=venue, store=store
    )

    assert held.position is None
    assert held.fills == ()


async def test_replayed_fills_put_a_parent_before_its_child_on_a_tie() -> None:
    """Stored unapplied fills with the same execution time replay parent first."""
    store = _NewestFirstStore()
    deployment, parent, child = await _pending_live_book(store)
    entry_fill = _remote(parent, name="f-entry", quantity="1", price="100", minutes=1)
    exit_fill = replace(
        _remote(child, name="f-tp", quantity="0.3", price="120", minutes=1),
        id=UUID(int=1),
    )
    await store.save_fill(replace(entry_fill, id=UUID(int=2)))
    await store.save_fill(exit_fill)
    venue = _Venue(fills={}, states=_bracketed_states("0.3"))

    result = await reconcile_open_orders(
        await store.get_deployment(deployment.id), broker=venue, store=store
    )

    assert result.position is not None
    assert result.position.quantity == Decimal("0.7")
