"""Actual-worker partial protection must reach independent cancellation authority."""

from dataclasses import replace
from decimal import Decimal

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from tests.execution.test_live_futures_entry import clock
from tests.execution.test_paper_futures_books import _PERP
from tests.execution_worker.futures_safety_support import cancels, closes, protect, step, stop_book
from thytrader.execution.futures_live import FuturesLiveRuntime, live_futures_runtime_scope
from thytrader.execution.futures_reduction import reduce_live_futures_position
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.submit import submit_intent
from thytrader.trading.futures_repair import managed_signed_base
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    IntentPurpose,
    LifecycleCommand,
    OrderKind,
    OrderSide,
    OrderStatus,
)


@pytest.mark.anyio
@pytest.mark.parametrize("direct_flatten", [False, True])
@pytest.mark.parametrize("partial_open", [False, True])
@pytest.mark.parametrize("command", [LifecycleCommand.FLATTEN, LifecycleCommand.MANAGED_SHUTDOWN])
@pytest.mark.parametrize("second_contracts", [1, 2])
async def test_overlapping_partial_protection_reaches_cancel_before_i9(
    monkeypatch: pytest.MonkeyPatch,
    direct_flatten: bool,
    partial_open: bool,
    command: LifecycleCommand,
    second_contracts: int,
) -> None:
    """Two ordinary owned stops flatten/flip without any overfill or forged projection."""
    venue = FakeVenue()
    clock(monkeypatch, venue)
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    snapshot, first = await protect(store, venue, snapshot)
    snapshot, second = await protect(store, venue, snapshot)
    assert first.venue_order_id is not None and second.venue_order_id is not None
    venue.fill(first.venue_order_id, Decimal(1))
    venue.orders[first.venue_order_id]["status"] = "OPEN" if partial_open else "CANCELLED"
    snapshot = await reconcile_open_orders(snapshot, broker=venue.broker(), store=store)
    assert snapshot.position is not None and snapshot.position.quantity == Decimal("0.01")
    venue.fill(second.venue_order_id, Decimal(second_contracts))
    if second_contracts == 1:
        venue.orders[second.venue_order_id]["status"] = "CANCELLED"
    snapshot = await reconcile_open_orders(snapshot, broker=venue.broker(), store=store)
    assert snapshot.position is None
    assert managed_signed_base(snapshot) == Decimal(1 - second_contracts) * venue.size
    await stop_book(store, snapshot, command)
    with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
        venue.advance()
        if direct_flatten:
            snapshot = await reduce_live_futures_position(
                await store.get_deployment(snapshot.deployment.id),
                account=await venue.read_account(frozenset()),
                broker=venue.broker(),
                store=store,
                purpose=IntentPurpose.STOP,
                now=venue.now,
            )
            assert cancels(venue) == int(partial_open)
            assert closes(venue) == 0, "positionless exit cannot manufacture a repair proof"
        snapshot = await step(store, venue, snapshot.deployment.id)
        assert cancels(venue) == int(partial_open)
        assert closes(venue) == 0, "cancellation alone cannot authorize a repair"
        # An identical observation is not the second fresh I9 mismatch.
        snapshot = await step(store, venue, snapshot.deployment.id)
        assert closes(venue) == 0
        for _ in range(4):
            venue.advance()
            snapshot = await step(store, venue, snapshot.deployment.id)
    assert venue.net == 0 and snapshot.position is None
    assert closes(venue) == second_contracts - 1
    assert cancels(venue) == int(partial_open)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "block", ["matching", "foreign", "unknown", "foreign_position", "identity", "ambiguous_exit"]
)
async def test_partial_protection_without_independent_orphan_authority_is_untouched(
    monkeypatch: pytest.MonkeyPatch, block: str
) -> None:
    """A partial fill alone never cancels healthy protection or implies exclusive ownership."""
    venue = FakeVenue()
    clock(monkeypatch, venue)
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    snapshot, first = await protect(store, venue, snapshot)
    snapshot, second = await protect(store, venue, snapshot)
    assert first.venue_order_id is not None and second.venue_order_id is not None
    venue.fill(first.venue_order_id, Decimal(1))
    venue.orders[first.venue_order_id]["status"] = "OPEN"
    if block != "matching":
        venue.fill(second.venue_order_id)
        if block in {"foreign", "unknown"}:
            venue.external = frozenset({_PERP}) if block == "foreign" else None
    snapshot = await reconcile_open_orders(snapshot, broker=venue.broker(), store=store)
    if block == "foreign_position":
        venue.net -= 1
    elif block == "identity":
        await store.save_order(replace(first, client_order_id="synthetic-unowned-protection"))
    elif block == "ambiguous_exit":
        venue.timeout_before_post = True
        unknown = await submit_intent(
            store=store,
            broker=venue.broker(),
            deployment_id=snapshot.deployment.id,
            product_id=_PERP,
            purpose=IntentPurpose.LIQUIDATION,
            side=OrderSide.BUY,
            kind=OrderKind.MARKETABLE,
            quantity=Decimal("0.01"),
            price=None,
            candle=None,
            decision_at=venue.now,
        )
        assert unknown.status is OrderStatus.UNKNOWN
    await stop_book(store, await store.get_deployment(snapshot.deployment.id))
    with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
        for _ in range(4):
            venue.advance()
            await step(store, venue, snapshot.deployment.id)
    assert cancels(venue) == 0
    # The setup's ambiguous attempt is recorded by the transport; the worker never resends it.
    assert closes(venue) == int(block == "ambiguous_exit")
    assert venue.orders[first.venue_order_id]["status"] == "OPEN"
