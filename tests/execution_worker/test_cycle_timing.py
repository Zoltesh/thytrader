"""Execution cycles are timed by phase and book without changing what they do (ADR 0131)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest

from thytrader.execution.paper import PaperBroker
from thytrader.execution_worker import service as worker_service
from thytrader.execution_worker.cycle_timing import CycleTimer, progress_heartbeat
from thytrader.execution_worker.service import _run_cycle, run_execution_worker
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.observability.execution_cycle import (
    CYCLE_PHASES,
    ExecutionCycleRecord,
    ExecutionCycleReport,
)
from thytrader.observability.venue_calls import record_venue_call
from thytrader.persistence.execution_cycles import (
    ExecutionCycleStore,
    ExecutionCycleStoreUnavailableError,
    InMemoryExecutionCycleStore,
)
from thytrader.persistence.worker_heartbeats import InMemoryWorkerHeartbeatStore
from thytrader.strategies.memory_store import InMemoryStrategyStore
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    RuntimePhase,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from thytrader.trading.models import DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore

pytestmark = pytest.mark.anyio
_NOW = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)


def _deployment(status: DeploymentStatus, product_id: str = "BTC-USD") -> Deployment:
    """One flat paper discretionary book in ``status``."""
    return Deployment(
        id=uuid4(),
        strategy_fingerprint=None,
        strategy_id=None,
        product_id=product_id,
        mode=DeploymentMode.PAPER,
        status=status,
        cash=Decimal("1000"),
        phase=RuntimePhase.FLAT,
        created_at=_NOW - timedelta(days=1),
        updated_at=_NOW,
        kind=DeploymentKind.DISCRETIONARY,
        timeframe="4h",
    )


async def _fleet(store: InMemoryExecutionStore) -> list[Deployment]:
    """Create one running, one paused and one stopped book."""
    books = [
        _deployment(DeploymentStatus.RUNNING, "BTC-USD"),
        _deployment(DeploymentStatus.PAUSED, "ETH-USD"),
        _deployment(DeploymentStatus.STOPPED, "SOL-USD"),
    ]
    for book in books:
        await store.create_deployment(book)
    return books


async def _cycle(
    store: InMemoryExecutionStore,
    *,
    on_book_done: Callable[[], Awaitable[None]] | None = None,
) -> ExecutionCycleReport | None:
    """Run one cycle on demo market data."""
    return await _run_cycle(
        store=store,
        publication_store=InMemoryStrategyStore(),
        market_data=MarketDataService(DemoMarketData()),
        paper_broker=PaperBroker(),
        live_broker=None,
        quote_reader=None,
        risk_store=None,
        worker_interval_seconds=30,
        on_book_done=on_book_done,
    )


async def test_cycle_report_times_every_phase_and_book_and_attributes_venue_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each visited book is timed with its venue calls; failures are flagged, not hidden."""
    store = InMemoryExecutionStore()
    books = await _fleet(store)
    failing = books[1].id
    processed: list[UUID] = []

    async def process(*, deployment_id: UUID, **kwargs: object) -> None:
        del kwargs
        processed.append(deployment_id)
        record_venue_call("GET", "/products/{id}/candles", 0.25, ok=True)
        if deployment_id == failing:
            raise RuntimeError("venue down")

    monkeypatch.setattr(worker_service, "_process_one", process)
    report = await _cycle(store)
    assert report is not None
    assert processed == [book.id for book in books]
    assert [phase.name for phase in report.phases] == list(CYCLE_PHASES)
    assert report.deployments_listed == 3
    assert (report.books.running, report.books.paused, report.books.stopped) == (1, 1, 1)
    assert report.book_failures == 1
    assert report.venue.requests == 3
    phases = {phase.name: phase for phase in report.phases}
    assert phases["books"].venue_requests == 3
    assert phases["risk_snapshots"].venue_requests == 0
    by_id = {book.deployment_id: book for book in report.slowest_books}
    assert set(by_id) == {book.id for book in books}
    assert by_id[failing].failed is True
    assert by_id[failing].product_id == "ETH-USD"
    assert by_id[failing].timeframe == "4h"
    assert by_id[failing].status == "paused"
    assert all(book.venue_requests == 1 for book in by_id.values())
    assert report.interval_seconds == 30
    assert report.slow is False


async def test_timing_leaves_the_book_loop_and_risk_reloads_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Books are visited in order with the evidence reload before and after each (n + 1)."""
    store = InMemoryExecutionStore()
    books = await _fleet(store)
    events: list[str] = []
    real_risk = worker_service._risk_snapshots

    async def risk(
        store: ExecutionStore, deployments: Sequence[Deployment]
    ) -> tuple[DeploymentSnapshot, ...]:
        events.append("risk")
        return await real_risk(store, deployments)

    async def process(*, deployment_id: UUID, **kwargs: object) -> None:
        del kwargs
        events.append(str(deployment_id))

    monkeypatch.setattr(worker_service, "_risk_snapshots", risk)
    monkeypatch.setattr(worker_service, "_process_one", process)
    beats: list[str] = []

    async def on_book_done() -> None:
        beats.append("beat")

    await _cycle(store, on_book_done=on_book_done)
    expected = ["risk"]
    for book in books:
        expected += [str(book.id), "risk"]
    assert events == expected
    assert beats == ["beat"] * len(books)


async def test_slowest_books_keep_only_the_ten_slowest() -> None:
    """The report bounds its book list and orders it slowest first."""
    timer = CycleTimer(cycle_id=uuid4(), started_at=_NOW, interval_seconds=30, window_cache=None)
    fleet = [_deployment(DeploymentStatus.RUNNING) for _ in range(14)]
    for index, deployment in enumerate(fleet):
        with timer.book(deployment):
            await asyncio.sleep(0.001 * (index % 5))
    report = timer.report(deployments=fleet, book_failures=0)
    assert len(report.slowest_books) == 10
    seconds = [book.seconds for book in report.slowest_books]
    assert seconds == sorted(seconds, reverse=True)
    assert report.slowest_phase == "books"


async def test_progress_heartbeat_is_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Between-book heartbeats touch at most every ten seconds."""
    clock = [100.0]
    monkeypatch.setattr("thytrader.execution_worker.cycle_timing.time.monotonic", lambda: clock[0])
    heartbeats = InMemoryWorkerHeartbeatStore()
    beat = progress_heartbeat(heartbeats)
    assert beat is not None
    await beat()
    assert await heartbeats.last_heartbeat("execution_worker") is None
    clock[0] += 10.5
    await beat()
    first = await heartbeats.last_heartbeat("execution_worker")
    assert first is not None
    clock[0] += 1
    await beat()
    assert await heartbeats.last_heartbeat("execution_worker") == first
    assert progress_heartbeat(None) is None


class _StoppingCycleStore(InMemoryExecutionCycleStore):
    """Record cycles and stop the worker after the first completed one."""

    def __init__(self, stop: asyncio.Event) -> None:
        """Bind the worker's stop event."""
        super().__init__()
        self._stop = stop

    async def complete_cycle(self, report: ExecutionCycleReport) -> None:
        """Store the report, then request shutdown."""
        await super().complete_cycle(report)
        self._stop.set()


class _BrokenCycleStore:
    """A cycle store whose storage is down."""

    def __init__(self, stop: asyncio.Event) -> None:
        """Bind the worker's stop event."""
        self._stop = stop

    async def start_cycle(
        self, cycle_id: UUID, started_at: datetime, interval_seconds: int
    ) -> None:
        """Fail like an unreachable database."""
        del cycle_id, started_at, interval_seconds
        raise ExecutionCycleStoreUnavailableError("down")

    async def complete_cycle(self, report: ExecutionCycleReport) -> None:
        """Fail like an unreachable database, then stop the worker."""
        del report
        self._stop.set()
        raise ExecutionCycleStoreUnavailableError("down")

    async def recent_cycles(self, limit: int) -> tuple[ExecutionCycleRecord, ...]:
        """Never read by the worker."""
        del limit
        return ()


async def _run_worker(
    cycle_store: ExecutionCycleStore, stop: asyncio.Event
) -> InMemoryWorkerHeartbeatStore:
    """Run the worker loop until the cycle store stops it."""
    store = InMemoryExecutionStore()
    await _fleet(store)
    heartbeats = InMemoryWorkerHeartbeatStore()
    await asyncio.wait_for(
        run_execution_worker(
            stop,
            store=store,
            publication_store=InMemoryStrategyStore(),
            market_data=MarketDataService(DemoMarketData()),
            paper_broker=PaperBroker(),
            live_broker=None,
            quote_reader=None,
            interval_seconds=1,
            heartbeat_store=heartbeats,
            cycle_store=cycle_store,
        ),
        timeout=30,
    )
    return heartbeats


async def test_worker_records_cycle_start_and_report() -> None:
    """The loop stores each cycle's start and its completed report, and heartbeats."""
    stop = asyncio.Event()
    cycles = _StoppingCycleStore(stop)
    heartbeats = await _run_worker(cycles, stop)
    records = await cycles.recent_cycles(5)
    assert len(records) == 1
    report = records[0].report
    assert report is not None
    assert records[0].completed_at == report.completed_at
    assert report.interval_seconds == 1
    assert report.books.running == 1
    assert report.window_cache.windows >= 0
    assert await heartbeats.last_heartbeat("execution_worker") is not None


async def test_worker_survives_cycle_storage_failures() -> None:
    """Telemetry storage failures never stop the worker loop."""
    stop = asyncio.Event()
    heartbeats = await _run_worker(_BrokenCycleStore(stop), stop)
    assert await heartbeats.last_heartbeat("execution_worker") is not None
