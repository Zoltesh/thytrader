"""Event-coordinated worker scheduling tests; no venue, wall-clock poll or concurrency."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal

import pytest

from thytrader.execution.paper import PaperBroker
from thytrader.execution_worker.service import run_execution_worker
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.persistence.worker_heartbeats import DisabledWorkerHeartbeatStore
from thytrader.strategies.snapshots import DisabledStrategySnapshotStore
from thytrader.trading.memory import InMemoryExecutionStore

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.persistence.worker_heartbeats import WorkerHeartbeatStore, WorkerName
    from thytrader.trading.models import Deployment


class ObservedEvent(asyncio.Event):
    """Expose waiter entry and cleanup without replacing asyncio scheduling."""

    def __init__(self) -> None:
        """Track all live event waiters."""
        super().__init__()
        self.waiting = asyncio.Event()
        self.active = 0

    async def wait(self) -> Literal[True]:
        """Signal idle arrival and count orphaned waiters on cancellation."""
        self.active += 1
        self.waiting.set()
        try:
            return await super().wait()
        finally:
            self.active -= 1


class CoordinatedStore(InMemoryExecutionStore):
    """Hold each real worker cycle at its deployment read until the test releases it."""

    def __init__(self) -> None:
        """Create explicit cycle entry/release channels."""
        super().__init__()
        self.entered: asyncio.Queue[int] = asyncio.Queue()
        self.release: asyncio.Queue[None] = asyncio.Queue()
        self.cycles = 0
        self.active = 0

    async def list_deployments(
        self, *, limit: int | None = None, offset: int = 0
    ) -> tuple[Deployment, ...]:
        """Prove cycles remain sequential while allowing feed/reload events mid-read."""
        del limit, offset
        self.active += 1
        assert self.active == 1
        self.cycles += 1
        self.entered.put_nowait(self.cycles)
        try:
            await self.release.get()
            return ()
        finally:
            self.active -= 1


def start_worker(
    store: CoordinatedStore,
    stop: ObservedEvent,
    wake: ObservedEvent,
    readiness: list[bool],
    heartbeat_store: WorkerHeartbeatStore | None = None,
) -> asyncio.Task[None]:
    """Start the public worker with a poll interval far beyond the test deadline."""
    return asyncio.create_task(
        run_execution_worker(
            stop,
            store=store,
            publication_store=DisabledStrategySnapshotStore(),
            market_data=MarketDataService(DemoMarketData()),
            paper_broker=PaperBroker(),
            live_broker=None,
            quote_reader=None,
            interval_seconds=3600,
            wake_requested=wake,
            on_readiness_changed=readiness.append,
            heartbeat_store=heartbeat_store,
        )
    )


class FirstHeartbeat(DisabledWorkerHeartbeatStore):
    """Hold the worker at its very first awaited operation."""

    def __init__(self) -> None:
        """Expose entry and release events for the initial heartbeat."""
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def touch(self, worker_name: WorkerName, at: datetime) -> None:
        """Allow the test to inject a nudge before any cycle body runs."""
        del worker_name, at
        self.entered.set()
        await self.release.wait()


@pytest.mark.anyio
async def test_consumption_precedes_first_await_and_preserves_heartbeat_nudge() -> None:
    """A pending startup wake is consumed, but a nudge during heartbeat I/O survives."""
    store, stop, wake = CoordinatedStore(), ObservedEvent(), ObservedEvent()
    heartbeat = FirstHeartbeat()
    wake.set()
    task = start_worker(store, stop, wake, [], heartbeat)
    try:
        await asyncio.wait_for(heartbeat.entered.wait(), 2)
        assert not wake.is_set()
        wake.set()
        heartbeat.release.set()
        assert await asyncio.wait_for(store.entered.get(), 2) == 1
        store.release.put_nowait(None)
        assert await asyncio.wait_for(store.entered.get(), 2) == 2
        stop.set()
        store.release.put_nowait(None)
        await asyncio.wait_for(task, 2)
        assert wake.active == stop.active == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
async def test_mid_cycle_burst_is_consumed_next_cycle_then_idle_wake() -> None:
    """A burst during an awaited cycle schedules exactly one immediate successor."""
    store, stop, wake = CoordinatedStore(), ObservedEvent(), ObservedEvent()
    readiness: list[bool] = []
    task = start_worker(store, stop, wake, readiness)
    try:
        assert await asyncio.wait_for(store.entered.get(), 1) == 1
        for _ in range(50):
            wake.set()
        store.release.put_nowait(None)
        assert await asyncio.wait_for(store.entered.get(), 1) == 2
        wake.waiting.clear()
        store.release.put_nowait(None)
        await asyncio.wait_for(wake.waiting.wait(), 1)
        # The idle waiter is entered only after cycle 2. A stale set bit would spin.
        for _ in range(10):
            await asyncio.sleep(0)
        assert store.cycles == 2
        assert not wake.is_set()
        wake.set()
        assert await asyncio.wait_for(store.entered.get(), 1) == 3
        stop.set()
        store.release.put_nowait(None)
        await asyncio.wait_for(task, 1)
        assert store.cycles == 3
        assert stop.active == wake.active == 0
        assert readiness == [True, False]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.anyio
@pytest.mark.parametrize("idle", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
async def test_worker_stop_and_cancel_leave_no_waiter_tasks(*, idle: bool, cancel: bool) -> None:
    """Idle and in-cycle shutdown await waiter cleanup and reset readiness."""
    store, stop, wake = CoordinatedStore(), ObservedEvent(), ObservedEvent()
    readiness: list[bool] = []
    task = start_worker(store, stop, wake, readiness)
    try:
        await asyncio.wait_for(store.entered.get(), 1)
        if idle:
            store.release.put_nowait(None)
            await asyncio.wait_for(wake.waiting.wait(), 1)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            stop.set()
            store.release.put_nowait(None)
            await asyncio.wait_for(task, 1)
        assert stop.active == wake.active == 0
        assert readiness == [True, False]
        assert store.cycles == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
