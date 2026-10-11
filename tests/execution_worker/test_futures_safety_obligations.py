"""Durable futures safety obligations survive diagnostics, process loss and delayed fills."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from tests.execution.test_live_futures_entry import clock
from tests.execution.test_paper_futures_books import _binding, _Observations
from tests.execution_worker.futures_safety_support import closes, protect, step, stop_book
from thytrader.execution.futures_live import (
    FuturesLiveRuntime,
    live_futures_runtime_scope,
    managed_live_futures,
)
from thytrader.execution.futures_paper import FuturesRuntime, futures_runtime_scope
from thytrader.trading.futures_book import InMemoryFuturesContractStore
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import LifecycleCommand

if TYPE_CHECKING:
    from collections.abc import Mapping


class TransientReadVenue(FakeVenue):
    """Untrusted synthetic JSON transport; fail only historical order GETs."""

    fail_order_read = False

    def get(self, path: str, params: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Inject a recoverable transport timeout, never a fabricated successful observation."""
        if (
            self.fail_order_read
            and "/historical/" in path
            and not path.endswith(("/fills", "/batch"))
        ):
            self.fail_order_read = False
            raise TimeoutError("synthetic transient order read")
        return super().get(path, params)


@pytest.mark.anyio
@pytest.mark.parametrize("faults", [0, 1, 3])
async def test_derisk_survives_reconcile_faults_and_restart(
    monkeypatch: pytest.MonkeyPatch,
    faults: int,
) -> None:
    """R1: recovered margin cannot discharge a previously persisted liquidation duty."""
    venue = TransientReadVenue()
    clock(monkeypatch, venue)
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    snapshot, _ = await protect(store, venue, snapshot)
    await stop_book(store, snapshot)
    contracts = InMemoryFuturesContractStore()
    await contracts.bind_contract(_binding(snapshot))
    venue.margin = Decimal(1)
    venue.cancel_confirmed = False
    with futures_runtime_scope(FuturesRuntime(contracts, _Observations(()))):
        with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
            pending = await step(store, venue, snapshot.deployment.id)
        assert (pending.deployment.mismatch_detail or "").startswith("FUTURES_DERISK_PENDING")
        assert closes(venue) == 0
        venue.margin = Decimal(100)
        for _ in range(faults):
            venue.fail_order_read = True
            venue.advance()
            with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
                await step(store, venue, snapshot.deployment.id)
        venue.cancel_confirmed = True
        for _ in range(3):
            venue.advance()
            with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
                current = await step(store, venue, snapshot.deployment.id)
        assert current.position is None and venue.net == 0
        assert closes(venue) == 1


class DelayedReductionVenue(FakeVenue):
    """Accepted reduction whose canonical fills arrive only when explicitly released."""

    def post(self, path: str, data: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Model transport acceptance separately from venue execution."""
        before_net, before_fills = self.net, len(self.fills)
        response = super().post(path, data)
        if path.endswith("/close_position"):
            self.net = before_net
            del self.fills[before_fills:]
            row = self.orders[response["success_response"]["order_id"]]
            row["status"], row["filled_size"] = "OPEN", "0"
        return response


@pytest.mark.anyio
@pytest.mark.parametrize("margin_breach", [False, True])
@pytest.mark.parametrize("delayed", [False, True])
async def test_flatten_requires_distinct_post_fill_sample(
    monkeypatch: pytest.MonkeyPatch,
    margin_breach: bool,
    delayed: bool,
) -> None:
    """R2/R3: neither fill timing nor de-risk completion can retire account reads."""
    venue = DelayedReductionVenue() if delayed else FakeVenue()
    clock(monkeypatch, venue)
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    await stop_book(store, snapshot, LifecycleCommand.FLATTEN)
    contracts = InMemoryFuturesContractStore()
    await contracts.bind_contract(_binding(snapshot))
    if margin_breach:
        venue.margin = Decimal(1)
    with futures_runtime_scope(FuturesRuntime(contracts, _Observations(()))):
        with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
            current = await step(store, venue, snapshot.deployment.id)
        if delayed:
            assert current.position is not None
            venue.advance()
            assert current.orders[-1].venue_order_id is not None
            venue.fill(current.orders[-1].venue_order_id)
            with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
                current = await step(store, venue, snapshot.deployment.id)
        assert current.position is None
        assert managed_live_futures(current)
        reads = venue.account_reads
        # Restart with a new runtime and adapter, without advancing the venue sample.
        with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
            current = await step(store, venue, snapshot.deployment.id)
        assert venue.account_reads > reads
        assert managed_live_futures(current)
        venue.advance(5)
        with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
            current = await step(store, venue, snapshot.deployment.id)
        assert managed_live_futures(current), "strictly later than fill + five seconds"
        venue.advance(1)
        with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
            current = await step(store, venue, snapshot.deployment.id)
        assert not managed_live_futures(current)
        assert closes(venue) == 1
