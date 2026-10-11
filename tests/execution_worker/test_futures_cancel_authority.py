"""Cancellation authority is distinct from authority to reduce a settled position."""

from dataclasses import replace
from decimal import Decimal

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from tests.execution.test_live_futures_entry import clock
from tests.execution.test_paper_futures_books import _binding, _Observations
from tests.execution_worker.futures_safety_support import cancels, closes, protect, step, stop_book
from thytrader.execution.futures_live import FuturesLiveRuntime, live_futures_runtime_scope
from thytrader.execution.futures_paper import FuturesRuntime, futures_runtime_scope
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.trading.futures_book import InMemoryFuturesContractStore
from thytrader.trading.memory import InMemoryExecutionStore


@pytest.mark.anyio
@pytest.mark.parametrize("partial", [False, True])
async def test_derisk_cancels_partially_executed_protection(
    monkeypatch: pytest.MonkeyPatch,
    partial: bool,
) -> None:
    """F1: legitimate OPEN partial stop must not veto its own cancellation forever."""
    venue = FakeVenue()
    clock(monkeypatch, venue)
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    snapshot, stop = await protect(store, venue, snapshot)
    assert stop.venue_order_id is not None
    if partial:
        venue.fill(stop.venue_order_id, Decimal(1))
        venue.orders[stop.venue_order_id]["status"] = "OPEN"
    await stop_book(store, snapshot)
    contracts = InMemoryFuturesContractStore()
    await contracts.bind_contract(_binding(snapshot))
    venue.margin = Decimal(1)
    with (
        futures_runtime_scope(FuturesRuntime(contracts, _Observations(()))),
        live_futures_runtime_scope(FuturesLiveRuntime(venue)),
    ):
        for _ in range(3):
            venue.advance()
            snapshot = await step(store, venue, snapshot.deployment.id)
    assert snapshot.position is None and venue.net == 0
    assert cancels(venue) == 1
    assert closes(venue) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("orphan", [False, True])
@pytest.mark.parametrize("overfill", [False, True])
async def test_proven_excess_cancels_protection_before_repair(
    monkeypatch: pytest.MonkeyPatch,
    orphan: bool,
    overfill: bool,
) -> None:
    """F2: repair a smaller projection or canonical wrong-way fill with orphan stop."""
    venue = FakeVenue()
    clock(monkeypatch, venue)
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    if overfill:
        snapshot, stop = await protect(store, venue, snapshot)
        if orphan:
            snapshot, _ = await protect(store, venue, snapshot)
        assert stop.venue_order_id is not None
        venue.fill(stop.venue_order_id, Decimal(3))
        snapshot = await reconcile_open_orders(snapshot, broker=venue.broker(), store=store)
        assert snapshot.position is None and venue.net == -1
    else:
        if orphan:
            snapshot, _ = await protect(store, venue, snapshot)
        assert snapshot.position is not None
        await store.save_position(
            replace(snapshot.position, quantity=Decimal("0.01")),
            deployment_id=snapshot.deployment.id,
        )
    await stop_book(store, await store.get_deployment(snapshot.deployment.id))
    contracts = InMemoryFuturesContractStore()
    await contracts.bind_contract(_binding(snapshot))
    with (
        futures_runtime_scope(FuturesRuntime(contracts, _Observations(()))),
        live_futures_runtime_scope(FuturesLiveRuntime(venue)),
    ):
        for _ in range(3):
            venue.advance()
            snapshot = await step(store, venue, snapshot.deployment.id)
    assert venue.net == (0 if overfill else 1)
    assert cancels(venue) == int(orphan)
    assert closes(venue) == 1
    assert (snapshot.deployment.mismatch_detail or "").startswith("FUTURES_POSITION_MISMATCH")
