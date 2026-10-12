"""Partial orphan race fills invalidate cancellation samples and rearm completed duties."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from tests.execution.test_live_futures_entry import clock
from tests.execution_worker.futures_safety_support import cancels, closes, protect, step, stop_book
from thytrader.execution.futures_live import (
    FuturesLiveRuntime,
    live_futures_runtime_scope,
    managed_live_futures,
)
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import LifecycleCommand

if TYPE_CHECKING:
    from collections.abc import Mapping


class PartialOrphanRaceVenue(FakeVenue):
    """Untrusted synthetic JSON boundary with one additional, non-overfilling stop execution."""

    race_order: str | None = None

    def remaining_fill(self, identity: str) -> None:
        """Publish the second contract with cumulative order size and incremental fill size."""
        previous = Decimal(self.orders[identity]["filled_size"])
        assert previous == 1 and Decimal(self.orders[identity]["base_size"]) == 2
        self.fill(identity, Decimal(1))
        self.orders[identity]["filled_size"] = str(previous + 1)

    def post(self, path: str, data: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Execute the final contract during cancel, before the venue confirms termination."""
        if path.endswith("/batch_cancel") and self.race_order is not None:
            self.remaining_fill(self.race_order)
            self.race_order = None
        return super().post(path, data)


@pytest.mark.anyio
@pytest.mark.parametrize("command", [LifecycleCommand.FLATTEN, LifecycleCommand.MANAGED_SHUTDOWN])
@pytest.mark.parametrize("delayed", [False, True])
async def test_partial_orphan_fill_during_cancel_or_after_verified_restart(
    monkeypatch: pytest.MonkeyPatch, command: LifecycleCommand, delayed: bool
) -> None:
    """Neither cancel authority nor an earlier flat verification authorizes stale repair."""
    venue = PartialOrphanRaceVenue()
    clock(monkeypatch, venue)
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    snapshot, first = await protect(store, venue, snapshot)
    snapshot, second = await protect(store, venue, snapshot)
    assert first.venue_order_id is not None and second.venue_order_id is not None
    venue.fill(first.venue_order_id, Decimal(1))
    venue.orders[first.venue_order_id]["status"] = "OPEN"
    snapshot = await reconcile_open_orders(snapshot, broker=venue.broker(), store=store)
    venue.fill(second.venue_order_id, Decimal(1))
    venue.orders[second.venue_order_id]["status"] = "CANCELLED"
    snapshot = await reconcile_open_orders(snapshot, broker=venue.broker(), store=store)
    await stop_book(store, snapshot, command)
    if not delayed:
        venue.race_order = first.venue_order_id
    venue.advance()
    with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
        current = await step(store, venue, snapshot.deployment.id)
    assert cancels(venue) == 1 and closes(venue) == 0
    if delayed:
        venue.advance()
        with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
            current = await step(store, venue, snapshot.deployment.id)
        assert not managed_live_futures(current)
        venue.advance()
        venue.remaining_fill(first.venue_order_id)
        # Reconcile outside the worker after its prior durable duty was discharged.
        current = await reconcile_open_orders(current, broker=venue.broker(), store=store)
    assert current.position is None and venue.net == -1
    assert current.deployment.futures_flat_confirmation_after == venue.now
    assert managed_live_futures(current)
    with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
        # Same observation cannot count; first new mismatch cannot repair either.
        await step(store, venue, snapshot.deployment.id)
        assert closes(venue) == 0
        venue.advance()
        await step(store, venue, snapshot.deployment.id)
        assert closes(venue) == 0
        venue.advance()
        current = await step(store, venue, snapshot.deployment.id)
        assert closes(venue) == 1 and venue.net == 0
        assert managed_live_futures(current)
        venue.advance()
        current = await step(store, venue, snapshot.deployment.id)
    assert not managed_live_futures(current)
    assert cancels(venue) == closes(venue) == 1
