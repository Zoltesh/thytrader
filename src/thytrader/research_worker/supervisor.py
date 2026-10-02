"""Supervisor that keeps ``research_worker_count`` worker processes alive (ADR 0092).

The supervisor itself never touches the database or the research stack: it spawns
one interpreter per slot (``spawn`` start method, so no event loop or connection is
ever inherited), restarts a slot whose process exits (immediately after a planned
recycle, with exponential backoff after a crash), logs each worker's RSS, and on
SIGTERM asks every worker to hand its job back before it stops. Leases, heartbeats,
and the slot rows operator health reads are the workers' own job.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import multiprocessing
import os
import secrets
import socket
import threading
import time
from typing import TYPE_CHECKING, Final

from thytrader.research.worker_pool import lease_lineage, lease_owner
from thytrader.research_worker.entry import worker_process_main
from thytrader.research_worker.memory import process_rss_bytes
from thytrader.research_worker.spec import (
    LogLevel,
    WorkerProcessSpec,
    heartbeat_interval,
    megabytes,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from multiprocessing.context import SpawnContext, SpawnProcess
    from pathlib import Path

    from pydantic import SecretStr

    from thytrader.config import Settings

_logger = logging.getLogger(__name__)
_STABLE_SECONDS: Final = 60.0
_MIB: Final = 1024 * 1024


@dataclass(frozen=True, slots=True)
class SupervisorConfig:
    """Resolved pool configuration the supervisor hands to every worker it spawns."""

    pool_size: int
    database_url: SecretStr
    dataset_root: Path
    log_level: LogLevel
    lease_seconds: float
    max_jobs: int
    max_rss_growth_bytes: int
    max_attempts: int
    readiness_file: Path | None = None
    poll_seconds: float = 0.5
    supervise_seconds: float = 0.5
    rss_log_seconds: float = 60.0
    stop_grace_seconds: float = 8.0
    max_backoff_seconds: float = 30.0

    @classmethod
    def from_settings(cls, settings: Settings) -> SupervisorConfig:
        """Resolve the pool from validated settings; PostgreSQL is required."""
        if settings.database_url is None:
            message = "THYTRADER_DATABASE_URL is required to run the research worker."
            raise ValueError(message)
        return cls(
            pool_size=settings.research_worker_count,
            database_url=settings.database_url,
            dataset_root=settings.market_data_dataset_root,
            log_level=settings.log_level,
            lease_seconds=float(settings.research_job_lease_seconds),
            max_jobs=settings.research_worker_max_jobs,
            max_rss_growth_bytes=megabytes(settings.research_worker_max_rss_growth_mb),
            max_attempts=settings.research_job_max_attempts,
            readiness_file=settings.research_worker_readiness_file,
        )


@dataclass(frozen=True, slots=True)
class SlotView:
    """Read-only view of one supervised slot (for logs and tests)."""

    slot: int
    pid: int | None
    alive: bool
    spawned: int
    crash_streak: int
    last_exit_code: int | None


@dataclass
class _Slot:
    """Mutable supervision state of one slot."""

    index: int
    process: SpawnProcess | None = None
    started_at: float = 0.0
    not_before: float = 0.0
    spawned: int = 0
    crash_streak: int = 0
    last_exit_code: int | None = None


class ResearchWorkerSupervisor:
    """Spawn, watch, restart, and stop the research worker processes."""

    def __init__(
        self,
        config: SupervisorConfig,
        *,
        target: Callable[[WorkerProcessSpec], None] = worker_process_main,
        host: str | None = None,
        supervisor_pid: int | None = None,
    ) -> None:
        """Prepare one slot per configured worker; nothing starts until :meth:`run`."""
        self._config = config
        self._target = target
        self._context: SpawnContext = multiprocessing.get_context("spawn")
        self._host = host if host is not None else socket.gethostname()
        self._supervisor_pid = supervisor_pid if supervisor_pid is not None else os.getpid()
        self._slots = [_Slot(index) for index in range(config.pool_size)]
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def request_stop(self) -> None:
        """Ask :meth:`run` to stop every worker and return (safe from signal handlers)."""
        self._stop.set()

    def slots(self) -> tuple[SlotView, ...]:
        """Return the current view of every slot."""
        with self._lock:
            return tuple(
                SlotView(
                    slot=slot.index,
                    pid=None if slot.process is None else slot.process.pid,
                    alive=slot.process is not None and slot.process.is_alive(),
                    spawned=slot.spawned,
                    crash_streak=slot.crash_streak,
                    last_exit_code=slot.last_exit_code,
                )
                for slot in self._slots
            )

    def run(self) -> None:
        """Keep the pool alive until :meth:`request_stop`, then stop it gracefully."""
        _logger.info(
            "research_worker_supervisor_started pool_size=%s pid=%s",
            self._config.pool_size,
            self._supervisor_pid,
        )
        try:
            with self._lock:
                for slot in self._slots:
                    self._spawn(slot, time.monotonic())
            _mark_ready(self._config.readiness_file)
            next_rss_log = time.monotonic() + self._config.rss_log_seconds
            while not self._stop.wait(timeout=self._config.supervise_seconds):
                now = time.monotonic()
                with self._lock:
                    for slot in self._slots:
                        self._supervise(slot, now)
                if now >= next_rss_log:
                    self._log_rss()
                    next_rss_log = now + self._config.rss_log_seconds
        finally:
            self._shutdown()
            _clear_ready(self._config.readiness_file)
            _logger.info("research_worker_supervisor_stopped")

    def _supervise(self, slot: _Slot, now: float) -> None:
        """Reap an exited process and respawn its slot when its backoff has passed."""
        process = slot.process
        if process is not None and process.is_alive():
            if slot.crash_streak and now - slot.started_at > _STABLE_SECONDS:
                slot.crash_streak = 0
            return
        if process is not None:
            self._reap(slot, process, now)
        if now >= slot.not_before:
            self._spawn(slot, now)

    def _reap(self, slot: _Slot, process: SpawnProcess, now: float) -> None:
        """Record one exited process and schedule its replacement."""
        process.join(timeout=0)
        code = process.exitcode
        process.close()
        slot.process = None
        slot.last_exit_code = code
        if code == 0:
            _logger.info("research_worker_recycled slot=%s", slot.index)
            slot.not_before = now
            return
        slot.crash_streak += 1
        backoff = min(self._config.max_backoff_seconds, float(2 ** (slot.crash_streak - 1)))
        slot.not_before = now + backoff
        _logger.warning(
            "research_worker_crashed slot=%s exit_code=%s restart_in_seconds=%.0f",
            slot.index,
            code,
            backoff,
        )

    def _spawn(self, slot: _Slot, now: float) -> None:
        """Start a fresh interpreter for one slot with a new lease owner token."""
        lineage = lease_lineage(self._host, self._supervisor_pid, slot.index)
        spec = WorkerProcessSpec(
            slot=slot.index,
            pool_size=self._config.pool_size,
            lineage=lineage,
            owner=lease_owner(lineage, secrets.token_hex(8)),
            database_url=self._config.database_url,
            dataset_root=self._config.dataset_root,
            log_level=self._config.log_level,
            lease_seconds=self._config.lease_seconds,
            heartbeat_seconds=heartbeat_interval(self._config.lease_seconds),
            poll_seconds=self._config.poll_seconds,
            max_jobs=self._config.max_jobs,
            max_rss_growth_bytes=self._config.max_rss_growth_bytes,
            max_attempts=self._config.max_attempts,
        )
        process = self._context.Process(
            target=self._target,
            args=(spec,),
            name=f"research-worker-{slot.index}",
        )
        process.start()
        slot.process = process
        slot.started_at = now
        slot.spawned += 1
        _logger.info(
            "research_worker_spawned slot=%s pid=%s spawned=%s",
            slot.index,
            process.pid,
            slot.spawned,
        )

    def _log_rss(self) -> None:
        """Log every live worker's resident set size."""
        for view in self.slots():
            if view.pid is None or not view.alive:
                continue
            rss = process_rss_bytes(view.pid)
            if rss is not None:
                _logger.info(
                    "research_worker_rss slot=%s pid=%s rss_mb=%.1f",
                    view.slot,
                    view.pid,
                    rss / _MIB,
                )

    def _shutdown(self) -> None:
        """SIGTERM every worker, wait out the grace period, then SIGKILL stragglers."""
        with self._lock:
            live = [slot.process for slot in self._slots if slot.process is not None]
            for process in live:
                if process.is_alive():
                    process.terminate()
            deadline = time.monotonic() + self._config.stop_grace_seconds
            for process in live:
                process.join(timeout=max(0.0, deadline - time.monotonic()))
            for process in live:
                if process.is_alive():
                    _logger.warning("research_worker_killed pid=%s", process.pid)
                    process.kill()
                    process.join(timeout=1.0)
            for slot in self._slots:
                slot.process = None


def _mark_ready(readiness_file: Path | None) -> None:
    """Create the optional supervisor-facing readiness marker."""
    if readiness_file is None:
        return
    readiness_file.parent.mkdir(parents=True, exist_ok=True)
    readiness_file.touch()


def _clear_ready(readiness_file: Path | None) -> None:
    """Remove the optional readiness marker during shutdown."""
    if readiness_file is None:
        return
    readiness_file.unlink(missing_ok=True)
