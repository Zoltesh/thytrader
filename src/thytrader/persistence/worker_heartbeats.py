"""Cross-process liveness records for supervised ThyTrader workers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime

WorkerName = Literal["portfolio_worker", "market_data_worker", "execution_worker"]
WORKER_NAMES: tuple[WorkerName, ...] = (
    "portfolio_worker",
    "market_data_worker",
    "execution_worker",
)


class WorkerHeartbeatUnavailableError(RuntimeError):
    """Signal that worker heartbeat storage is unavailable."""


@runtime_checkable
class WorkerHeartbeatStore(Protocol):
    """Persist the latest heartbeat for each named worker process."""

    async def touch(self, worker_name: WorkerName, at: datetime) -> None:
        """Record that one worker is alive at ``at``."""
        ...

    async def last_heartbeat(self, worker_name: WorkerName) -> datetime | None:
        """Return the latest heartbeat instant, or None when none has been recorded."""
        ...


class DisabledWorkerHeartbeatStore:
    """No-op writer used when PostgreSQL is not configured."""

    async def touch(self, worker_name: WorkerName, at: datetime) -> None:
        """Ignore heartbeats when durable storage is unavailable."""
        del worker_name, at

    async def last_heartbeat(self, worker_name: WorkerName) -> datetime | None:
        """Refuse to invent liveness when durable storage is unavailable."""
        del worker_name
        raise WorkerHeartbeatUnavailableError("Worker heartbeat storage is unavailable.")


class InMemoryWorkerHeartbeatStore:
    """Deterministic heartbeat map for tests."""

    def __init__(self) -> None:
        """Start with no recorded heartbeats."""
        self._heartbeats: dict[WorkerName, datetime] = {}

    async def touch(self, worker_name: WorkerName, at: datetime) -> None:
        """Store one worker's latest heartbeat."""
        self._heartbeats[worker_name] = at

    async def last_heartbeat(self, worker_name: WorkerName) -> datetime | None:
        """Return the stored heartbeat when present."""
        return self._heartbeats.get(worker_name)
