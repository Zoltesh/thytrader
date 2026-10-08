"""Lease renewal and slot heartbeats on a dedicated thread (ADR 0092).

Simulation segments run on worker threads and hold the GIL in long pure-Python loops,
and some study steps (stitching, large result validation) run on the event loop
itself. A lease renewed from that loop could miss its deadline while the process is
perfectly healthy. The heartbeat therefore runs on its own thread with its own event
loop and database engine: it renews the current job's lease, writes the slot's
self-report (state, job, RSS), and periodically sweeps expired leases and overdue jobs.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
import logging
import threading
from typing import TYPE_CHECKING, Final

from sqlalchemy.exc import SQLAlchemyError

from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_portfolios import PostgresPortfolioStore
from thytrader.persistence.postgres_research_jobs import (
    PostgresResearchJobStore,
    ResearchJobUnavailableError,
)
from thytrader.persistence.postgres_research_queue import (
    PostgresResearchQueue,
    ResearchQueueUnavailableError,
)
from thytrader.portfolios.errors import PortfolioError
from thytrader.research.worker_pool import (
    ClaimedResearchJob,
    ResearchWorkerSlot,
    ResearchWorkerState,
)
from thytrader.research_worker.memory import process_rss_bytes

if TYPE_CHECKING:
    from thytrader.research_worker.spec import WorkerProcessSpec

_logger = logging.getLogger(__name__)
_SWEEP_EVERY_BEATS: Final = 3
_STOP_POLL_SECONDS: Final = 0.1
_BEAT_ERRORS: Final = (
    ResearchQueueUnavailableError,
    ResearchJobUnavailableError,
    PortfolioError,
    SQLAlchemyError,
    OSError,
)


@dataclass
class WorkerStatus:
    """Thread-safe state of one worker process, written by its main loop."""

    slot: int
    pool_size: int
    pid: int
    started_at: datetime
    _state: ResearchWorkerState = "starting"
    _job: ClaimedResearchJob | None = None
    _jobs_completed: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def current(self) -> ClaimedResearchJob | None:
        """Return the job this process is running, if any."""
        with self._lock:
            return self._job

    @property
    def jobs_completed(self) -> int:
        """Return how many jobs this process has finished."""
        with self._lock:
            return self._jobs_completed

    def running(self, claimed: ClaimedResearchJob) -> None:
        """Record that this process claimed ``claimed``."""
        with self._lock:
            self._state = "running"
            self._job = claimed

    def finished(self) -> None:
        """Record that the current job ended (any outcome) and the process is idle."""
        with self._lock:
            self._state = "idle"
            self._job = None
            self._jobs_completed += 1

    def idle(self) -> None:
        """Record that the process is waiting for work."""
        with self._lock:
            self._state = "idle"
            self._job = None

    def stopping(self) -> None:
        """Record that the process is shutting down or recycling."""
        with self._lock:
            self._state = "stopping"

    def report(self, rss_bytes: int | None) -> ResearchWorkerSlot:
        """Build the slot row this process publishes."""
        with self._lock:
            job = self._job
            return ResearchWorkerSlot(
                slot=self.slot,
                pool_size=self.pool_size,
                pid=self.pid,
                state=self._state,
                job_id=None if job is None else job.job_id,
                job_kind=None if job is None else job.kind,
                jobs_completed=self._jobs_completed,
                rss_bytes=rss_bytes,
                started_at=self.started_at,
                heartbeat_at=datetime.now(UTC),
            )


class LeaseHeartbeat:
    """Renew the current lease and publish the slot report from a dedicated thread."""

    def __init__(self, spec: WorkerProcessSpec, status: WorkerStatus) -> None:
        """Prepare (but do not start) the heartbeat thread for one worker process."""
        self._spec = spec
        self._status = status
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"research-lease-{spec.slot}", daemon=True
        )

    def start(self) -> None:
        """Start beating."""
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Ask the thread to publish a last report and stop, waiting up to ``timeout``."""
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout)

    def _run(self) -> None:
        """Thread body: run the heartbeat loop on this thread's own event loop."""
        asyncio.run(self._loop())

    async def _loop(self) -> None:
        """Beat until stopped, then publish one final report."""
        engine = create_engine(self._spec.database_url)
        queue = PostgresResearchQueue(engine)
        jobs = PostgresResearchJobStore(engine)
        portfolios = PostgresPortfolioStore(engine)
        beat = 0
        try:
            while not self._stop.is_set():
                await self._beat(queue, jobs, portfolios, sweep=beat % _SWEEP_EVERY_BEATS == 0)
                beat += 1
                await self._pause()
            await self._publish(queue)
        finally:
            await dispose(engine)

    async def _pause(self) -> None:
        """Sleep one heartbeat interval, waking early when asked to stop."""
        remaining = self._spec.heartbeat_seconds
        while remaining > 0 and not self._stop.is_set():
            step = min(_STOP_POLL_SECONDS, remaining)
            await asyncio.sleep(step)
            remaining -= step

    async def _beat(
        self,
        queue: PostgresResearchQueue,
        jobs: PostgresResearchJobStore,
        portfolios: PostgresPortfolioStore,
        *,
        sweep: bool,
    ) -> None:
        """Renew, publish, and (every few beats) sweep; storage errors are logged only."""
        try:
            if self._status.current is not None:
                await queue.renew(self._spec.owner, lease_seconds=self._spec.lease_seconds)
            await queue.record_slot(self._status.report(process_rss_bytes()))
            if sweep:
                await self._sweep(queue, jobs, portfolios)
        except _BEAT_ERRORS as error:
            _logger.warning(
                "research_worker_heartbeat_failed slot=%s error_class=%s",
                self._spec.slot,
                type(error).__name__,
            )

    async def _sweep(
        self,
        queue: PostgresResearchQueue,
        jobs: PostgresResearchJobStore,
        portfolios: PostgresPortfolioStore,
    ) -> None:
        """Re-queue rows of dead workers and expire jobs past their 24-hour expiry."""
        counts = await queue.requeue_expired(max_attempts=self._spec.max_attempts)
        if counts.total:
            _logger.info(
                "research_leases_swept requeued=%s cancelled=%s failed=%s",
                counts.requeued,
                counts.cancelled,
                counts.failed,
            )
        await jobs.expire_stale()
        await portfolios.expire_stale()

    async def _publish(self, queue: PostgresResearchQueue) -> None:
        """Publish the final slot report on the way out."""
        try:
            await queue.record_slot(self._status.report(process_rss_bytes()))
        except _BEAT_ERRORS:
            _logger.debug("research_worker_final_report_failed slot=%s", self._spec.slot)
