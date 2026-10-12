"""Accepted cancellation survives confirmation outages and fresh worker runtimes."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from tests.execution.test_live_futures_entry import clock
from tests.execution_worker.futures_safety_support import cancels, closes, protect, step, stop_book
from thytrader.exchanges.rest_transport import CoinbaseHttpStatusError
from thytrader.execution.broker import CANCEL_PENDING_REASON
from thytrader.execution.futures_live import FuturesLiveRuntime, live_futures_runtime_scope
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.trading.futures_repair import managed_signed_base
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentSnapshot, LifecycleCommand

if TYPE_CHECKING:
    from collections.abc import Mapping

    from thytrader.trading.store import ExecutionStore


class ConfirmationFailureVenue(FakeVenue):
    """Fail only post-acceptance order reads, never the pre-send identity proof.

    Any is confined to synthetic untrusted transport JSON, as in FakeVenue.
    """

    def __init__(self, fault: str = "once") -> None:
        """Select a one-off, persistent, typed HTTP or readable confirmation control."""
        super().__init__()
        self.fault = fault
        self.fail_confirmation = False
        self.race_fill = False

    def post(self, path: str, data: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Accept the real synthetic cancel before losing its observation."""
        result = super().post(path, data)
        if path.endswith("/batch_cancel"):
            self.fail_confirmation = self.fault != "readable"
            if self.race_fill:
                assert data is not None and isinstance(data["order_ids"], list)
                identity = str(data["order_ids"][0])
                self.fill(identity, Decimal(1))
                # FakeVenue.fill describes the incremental execution; history is cumulative.
                self.orders[identity]["filled_size"] = "2"
        return result

    def get(self, path: str, params: Mapping[str, object] | None = None) -> dict[str, Any]:
        """A failed confirmation contributes no successful order evidence."""
        if (
            self.fail_confirmation
            and "/historical/" in path
            and not path.endswith(("/fills", "/batch"))
        ):
            if self.fault == "once":
                self.fail_confirmation = False
            if self.fault == "http":
                raise CoinbaseHttpStatusError(503, "UNAVAILABLE")
            raise TimeoutError("synthetic confirmation unavailable")
        return super().get(path, params)


async def overlapping_stops(
    store: ExecutionStore, venue: FakeVenue, snapshot: DeploymentSnapshot
) -> DeploymentSnapshot:
    """Produce flat local inventory and owned opposite excess from legitimate stop fills."""
    snapshot, first = await protect(store, venue, snapshot)
    snapshot, second = await protect(store, venue, snapshot)
    assert first.venue_order_id is not None and second.venue_order_id is not None
    venue.fill(first.venue_order_id, Decimal(1))
    venue.orders[first.venue_order_id]["status"] = "OPEN"
    snapshot = await reconcile_open_orders(snapshot, broker=venue.broker(), store=store)
    venue.fill(second.venue_order_id)
    snapshot = await reconcile_open_orders(snapshot, broker=venue.broker(), store=store)
    assert snapshot.position is None and managed_signed_base(snapshot) == -venue.size
    return snapshot


async def exercise_cancel_outage(
    store: ExecutionStore,
    venue: ConfirmationFailureVenue,
    snapshot: DeploymentSnapshot,
    command: LifecycleCommand,
    reload_store: ExecutionStore,
) -> None:
    """Use fresh adapters/runtimes and persisted rows through pending and terminal recovery."""
    snapshot = await overlapping_stops(store, venue, snapshot)
    await stop_book(store, snapshot, command)
    venue.cancel_confirmed = False
    for _ in range(3):
        venue.advance()
        # No runtime comparison counter or broker object survives the restart boundary.
        with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
            current = await step(reload_store, venue, snapshot.deployment.id)
        assert closes(venue) == 0
        assert cancels(venue) == 1
        if not venue.race_fill:
            assert any(order.reject_reason == CANCEL_PENDING_REASON for order in current.orders)
    venue.fail_confirmation = False
    venue.cancel_confirmed = True
    with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
        for _ in range(6):
            venue.advance()
            current = await step(reload_store, venue, snapshot.deployment.id)
    assert cancels(venue) == 1 and closes(venue) == 1
    assert venue.net == 0 and managed_signed_base(current) == 0
    assert current.position is None
    assert current.deployment.futures_flat_confirmation_after is None
    assert all(fill.economics_applied_at is not None for fill in current.fills)
    expected_cash = sum(
        (
            Decimal(fill["price"])
            * Decimal(fill["size"])
            * venue.size
            * (1 if fill["side"] == "SELL" else -1)
            - Decimal(fill["commission"])
            for fill in venue.fills
        ),
        Decimal(0),
    )
    assert current.deployment.cash == expected_cash
    assert len(current.fills) == len(venue.fills)
    assert sum((fill.fee for fill in current.fills), Decimal(0)) == sum(
        (Decimal(fill["commission"]) for fill in venue.fills), Decimal(0)
    )


@pytest.mark.anyio
@pytest.mark.parametrize("fault", ["readable", "once", "persistent", "http"])
@pytest.mark.parametrize("command", [LifecycleCommand.MANAGED_SHUTDOWN, LifecycleCommand.FLATTEN])
@pytest.mark.parametrize("race", [False, True])
async def test_accepted_cancel_read_failure_never_resends(
    monkeypatch: pytest.MonkeyPatch, fault: str, command: LifecycleCommand, race: bool
) -> None:
    """Acceptance is durable independently of confirmation, including canonical race fills."""
    venue = ConfirmationFailureVenue(fault)
    clock(monkeypatch, venue)
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    venue.race_fill = race
    await exercise_cancel_outage(store, venue, snapshot, command, store)
