"""Measure one execution-worker cycle by phase and by book (ADR 0131).

``CycleTimer`` wraps the cycle's phases and books with wall-clock probes, attributes the
venue REST calls and database statements recorded in its bound ledgers and the
deploy-window cache's range fetches and warming to each, and builds the cycle's
``ExecutionCycleReport``. It only observes: it never changes what a phase or a book
does, and the cycle survives any failure to build or store its report.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import heapq
import time
from typing import TYPE_CHECKING

from thytrader.execution_worker.ports import _logger
from thytrader.market_data.window_cache import DeployWindowCache
from thytrader.observability.database_calls import DatabaseCallLedger, database_call_scope
from thytrader.observability.execution_cycle import (
    CYCLE_PHASES,
    MAX_REPORTED_BOOKS,
    BookGroupTiming,
    BookStatusCounts,
    CycleBookTiming,
    CyclePhaseName,
    CyclePhaseTiming,
    DatabaseCallSummary,
    ExecutionCycleReport,
    WindowCacheCycleState,
)
from thytrader.observability.venue_calls import CallTotals, VenueCallLedger, venue_call_scope
from thytrader.persistence.execution_cycles import ExecutionCycleStoreUnavailableError
from thytrader.persistence.worker_heartbeats import WorkerHeartbeatUnavailableError
from thytrader.trading.models import DeploymentStatus

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator, Sequence
    from uuid import UUID

    from thytrader.market_data.service import MarketDataService
    from thytrader.market_data.window_cache import WindowCacheStats
    from thytrader.persistence.execution_cycles import ExecutionCycleStore
    from thytrader.persistence.worker_heartbeats import WorkerHeartbeatStore
    from thytrader.trading.models import Deployment

_ZERO = CallTotals(0, 0.0)


@dataclass(frozen=True, slots=True)
class _Reading:
    """Venue and database totals at one instant, for phase and book deltas."""

    venue: CallTotals
    database: CallTotals

    def since(self, earlier: _Reading) -> _Reading:
        """Return the calls made between ``earlier`` and this reading."""
        return _Reading(self.venue.since(earlier.venue), self.database.since(earlier.database))

    def plus(self, other: _Reading) -> _Reading:
        """Add two deltas."""
        return _Reading(
            CallTotals(
                self.venue.requests + other.venue.requests,
                self.venue.seconds + other.venue.seconds,
            ),
            CallTotals(
                self.database.requests + other.database.requests,
                self.database.seconds + other.database.seconds,
            ),
        )


@dataclass(slots=True)
class _PhaseTotals:
    """Accumulated wall time, venue and database traffic for one phase."""

    seconds: float = 0.0
    calls: _Reading = _Reading(_ZERO, _ZERO)


@dataclass(slots=True)
class BookProbe:
    """Outcome flags a book's processing sets while it is being timed."""

    failed: bool = False


class CycleTimer:
    """Accumulate phase and book timings for one cycle."""

    def __init__(
        self,
        *,
        cycle_id: UUID,
        started_at: datetime,
        interval_seconds: int,
        window_cache: DeployWindowCache | None,
    ) -> None:
        """Start the cycle clock with fresh venue and database ledgers."""
        self.cycle_id = cycle_id
        self.started_at = started_at
        self.interval_seconds = max(interval_seconds, 1)
        self.venue = VenueCallLedger()
        self.database = DatabaseCallLedger()
        self._window_cache = window_cache
        self._cache_start = self._cache_stats()
        self._clock_start = time.perf_counter()
        self._phases: dict[CyclePhaseName, _PhaseTotals] = {
            name: _PhaseTotals() for name in CYCLE_PHASES
        }
        self._books: list[tuple[float, int, CycleBookTiming]] = []
        self._groups: dict[tuple[str, str], tuple[int, float, _Reading]] = {}
        self._book_sequence = 0
        self._warming_books = 0

    @contextmanager
    def bound(self) -> Iterator[None]:
        """Record venue requests and database statements made in this context."""
        with venue_call_scope(self.venue), database_call_scope(self.database):
            yield

    @contextmanager
    def phase(self, name: CyclePhaseName) -> Iterator[None]:
        """Add the wall time, venue and database calls of the wrapped block to ``name``."""
        before = self._reading()
        started = time.perf_counter()
        try:
            yield
        finally:
            totals = self._phases[name]
            totals.seconds += time.perf_counter() - started
            totals.calls = totals.calls.plus(self._reading().since(before))

    @contextmanager
    def book(self, deployment: Deployment) -> Iterator[BookProbe]:
        """Time one book within the ``books`` phase and keep it if among the slowest."""
        probe = BookProbe()
        cache_before = self._cache_stats()
        with self.phase("books"):
            before = self._reading()
            started = time.perf_counter()
            try:
                yield probe
            finally:
                seconds = time.perf_counter() - started
                calls = self._reading().since(before)
                self._keep_book(deployment, probe, seconds, calls, cache_before)

    def report(
        self,
        *,
        deployments: Sequence[Deployment],
        book_failures: int,
        shared_reads: int = 0,
        completed_at: datetime | None = None,
    ) -> ExecutionCycleReport:
        """Build the cycle report from everything timed so far."""
        duration = time.perf_counter() - self._clock_start
        cache_now = self._cache_stats()
        phases = tuple(
            CyclePhaseTiming(
                name=name,
                seconds=round(totals.seconds, 3),
                venue_requests=totals.calls.venue.requests,
                venue_seconds=round(totals.calls.venue.seconds, 3),
                db_statements=totals.calls.database.requests,
                db_seconds=round(totals.calls.database.seconds, 3),
            )
            for name, totals in self._phases.items()
        )
        slowest = max(phases, key=lambda phase: phase.seconds)
        books = [timing for _seconds, _order, timing in self._books]
        books.sort(key=lambda timing: (-timing.seconds, str(timing.deployment_id)))
        database = self.database.totals()
        return ExecutionCycleReport(
            cycle_id=self.cycle_id,
            started_at=self.started_at,
            completed_at=completed_at or datetime.now(UTC),
            duration_seconds=round(duration, 3),
            interval_seconds=self.interval_seconds,
            deployments_listed=len(deployments),
            books=_status_counts(deployments),
            book_failures=book_failures,
            slowest_phase=slowest.name,
            phases=phases,
            slowest_books=tuple(books),
            venue=self.venue.summary(),
            database=DatabaseCallSummary(
                statements=database.requests, seconds=round(database.seconds, 3)
            ),
            window_cache=WindowCacheCycleState(
                windows=0 if cache_now is None else cache_now.windows,
                cached_candles=0 if cache_now is None else cache_now.cached_candles,
                range_requests=_cache_delta(self._cache_start, cache_now, _range_requests),
                warming_events=_cache_delta(self._cache_start, cache_now, _warming_events),
                warming_books=self._warming_books,
            ),
            book_groups=tuple(
                BookGroupTiming(
                    status=status,
                    mode=mode,
                    books=count,
                    seconds=round(seconds, 3),
                    venue_requests=calls.venue.requests,
                    venue_seconds=round(calls.venue.seconds, 3),
                    db_statements=calls.database.requests,
                    db_seconds=round(calls.database.seconds, 3),
                )
                for (status, mode), (count, seconds, calls) in sorted(self._groups.items())
            ),
            shared_reads=shared_reads,
        )

    def _reading(self) -> _Reading:
        """Read both ledgers."""
        return _Reading(self.venue.totals(), self.database.totals())

    def _keep_book(
        self,
        deployment: Deployment,
        probe: BookProbe,
        seconds: float,
        calls: _Reading,
        cache_before: WindowCacheStats | None,
    ) -> None:
        """Record one book's timing, retaining only the slowest few."""
        cache_after = self._cache_stats()
        warming = _cache_delta(cache_before, cache_after, _warming_events) > 0
        if warming:
            self._warming_books += 1
        timing = CycleBookTiming(
            deployment_id=deployment.id,
            product_id=deployment.product_id[:64],
            timeframe=None if deployment.timeframe is None else deployment.timeframe[:16],
            status=deployment.status.value,
            mode=deployment.mode.value,
            kind=deployment.kind.value,
            seconds=round(seconds, 3),
            venue_requests=calls.venue.requests,
            venue_seconds=round(calls.venue.seconds, 3),
            db_statements=calls.database.requests,
            db_seconds=round(calls.database.seconds, 3),
            window_range_requests=_cache_delta(cache_before, cache_after, _range_requests),
            warming=warming,
            failed=probe.failed,
        )
        group = (timing.status, timing.mode)
        count, total, group_calls = self._groups.get(group, (0, 0.0, _Reading(_ZERO, _ZERO)))
        self._groups[group] = (count + 1, total + seconds, group_calls.plus(calls))
        self._book_sequence += 1
        entry = (seconds, -self._book_sequence, timing)
        if len(self._books) < MAX_REPORTED_BOOKS:
            heapq.heappush(self._books, entry)
        else:
            heapq.heappushpop(self._books, entry)

    def _cache_stats(self) -> WindowCacheStats | None:
        """Read the cache counters, or None when this cycle has no window cache."""
        return None if self._window_cache is None else self._window_cache.stats()


def _range_requests(stats: WindowCacheStats) -> int:
    """Read the cumulative provider range-fetch counter."""
    return stats.range_requests


def _warming_events(stats: WindowCacheStats) -> int:
    """Read the cumulative warming counter."""
    return stats.warming_events


def _cache_delta(
    before: WindowCacheStats | None,
    after: WindowCacheStats | None,
    counter: Callable[[WindowCacheStats], int],
) -> int:
    """Return how much one cumulative cache counter grew, never negative."""
    if before is None or after is None:
        return 0
    return max(counter(after) - counter(before), 0)


def _status_counts(deployments: Sequence[Deployment]) -> BookStatusCounts:
    """Count the RUNNING, PAUSED and STOPPED books the cycle visits."""
    statuses = [deployment.status for deployment in deployments]
    return BookStatusCounts(
        running=statuses.count(DeploymentStatus.RUNNING),
        paused=statuses.count(DeploymentStatus.PAUSED),
        stopped=statuses.count(DeploymentStatus.STOPPED),
    )


_PROGRESS_HEARTBEAT_SECONDS = 10.0


def window_cache_of(market_data: MarketDataService) -> DeployWindowCache | None:
    """Return the cycle's deploy-window cache, or None for a market-data double without one."""
    cache = getattr(market_data, "window_cache", None)
    return cache if isinstance(cache, DeployWindowCache) else None


def progress_heartbeat(
    heartbeat_store: WorkerHeartbeatStore | None,
) -> Callable[[], Awaitable[None]] | None:
    """Refresh the worker heartbeat between books, at most every 10 seconds.

    The heartbeat then means the loop is making progress; one book that blocks for longer
    than the stale window still lets it go stale. Storage failures are logged and skipped
    here: the cycle-start touch remains the fail-loud liveness write.
    """
    if heartbeat_store is None:
        return None
    last = time.monotonic()

    async def beat() -> None:
        """Touch the heartbeat when the throttle window has passed."""
        nonlocal last
        now = time.monotonic()
        if now - last < _PROGRESS_HEARTBEAT_SECONDS:
            return
        last = now
        try:
            await heartbeat_store.touch("execution_worker", datetime.now(UTC))
        except WorkerHeartbeatUnavailableError:
            _logger.warning("execution_progress_heartbeat_failed")

    return beat


async def record_cycle_start(cycle_store: ExecutionCycleStore | None, timer: CycleTimer) -> None:
    """Record that a cycle started; telemetry failures never stop the cycle."""
    if cycle_store is None:
        return
    try:
        await cycle_store.start_cycle(timer.cycle_id, timer.started_at, timer.interval_seconds)
    except ExecutionCycleStoreUnavailableError:
        _logger.warning("execution_cycle_start_unrecorded cycle_id=%s", timer.cycle_id)


async def record_cycle_report(
    cycle_store: ExecutionCycleStore | None, report: ExecutionCycleReport | None
) -> None:
    """Log one cycle's timing and store its report; telemetry failures never stop the worker."""
    if report is None:
        return
    slowest = report.slowest_books[0] if report.slowest_books else None
    log = _logger.warning if report.slow else _logger.info
    log(
        "execution_cycle_completed duration=%.1fs interval=%ss slowest_phase=%s "
        "venue_requests=%s venue_seconds=%.1f db_statements=%s db_seconds=%.1f "
        "books=%s/%s/%s warming_books=%s "
        "slowest_book=%s slowest_book_seconds=%.1f",
        report.duration_seconds,
        report.interval_seconds,
        report.slowest_phase,
        report.venue.requests,
        report.venue.seconds,
        report.database.statements,
        report.database.seconds,
        report.books.running,
        report.books.paused,
        report.books.stopped,
        report.window_cache.warming_books,
        None if slowest is None else slowest.deployment_id,
        0.0 if slowest is None else slowest.seconds,
    )
    if cycle_store is None:
        return
    try:
        await cycle_store.complete_cycle(report)
    except ExecutionCycleStoreUnavailableError:
        _logger.warning("execution_cycle_report_unrecorded cycle_id=%s", report.cycle_id)
