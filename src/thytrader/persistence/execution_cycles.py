"""Storage contract for execution-worker cycle timing records (ADR 0131)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from thytrader.observability.execution_cycle import ExecutionCycleRecord

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from thytrader.observability.execution_cycle import ExecutionCycleReport

MAX_RECENT_CYCLES = 200


class ExecutionCycleStoreUnavailableError(RuntimeError):
    """Signal that execution cycle storage is unavailable."""


@runtime_checkable
class ExecutionCycleStore(Protocol):
    """Record each cycle's start and completion; read the most recent cycles."""

    async def start_cycle(
        self, cycle_id: UUID, started_at: datetime, interval_seconds: int
    ) -> None:
        """Record that a cycle started, so a long-running cycle is visible before it ends."""
        ...

    async def complete_cycle(self, report: ExecutionCycleReport) -> None:
        """Attach the finished cycle's report to its started record."""
        ...

    async def recent_cycles(self, limit: int) -> tuple[ExecutionCycleRecord, ...]:
        """Return up to ``limit`` cycles, newest start first."""
        ...


class DisabledExecutionCycleStore:
    """No-op writer used when PostgreSQL is not configured."""

    async def start_cycle(
        self, cycle_id: UUID, started_at: datetime, interval_seconds: int
    ) -> None:
        """Ignore cycle starts when durable storage is unavailable."""
        del cycle_id, started_at, interval_seconds

    async def complete_cycle(self, report: ExecutionCycleReport) -> None:
        """Ignore cycle reports when durable storage is unavailable."""
        del report

    async def recent_cycles(self, limit: int) -> tuple[ExecutionCycleRecord, ...]:
        """Refuse to invent cycle timing when durable storage is unavailable."""
        del limit
        raise ExecutionCycleStoreUnavailableError("Execution cycle storage is unavailable.")


class InMemoryExecutionCycleStore:
    """Deterministic cycle records for tests."""

    def __init__(self) -> None:
        """Start with no recorded cycles."""
        self._records: dict[UUID, ExecutionCycleRecord] = {}

    async def start_cycle(
        self, cycle_id: UUID, started_at: datetime, interval_seconds: int
    ) -> None:
        """Store one started cycle."""
        self._records[cycle_id] = ExecutionCycleRecord(
            cycle_id=cycle_id, started_at=started_at, interval_seconds=max(interval_seconds, 1)
        )

    async def complete_cycle(self, report: ExecutionCycleReport) -> None:
        """Store the report, creating the record when its start was not stored."""
        self._records[report.cycle_id] = ExecutionCycleRecord(
            cycle_id=report.cycle_id,
            started_at=report.started_at,
            interval_seconds=report.interval_seconds,
            completed_at=report.completed_at,
            report=report,
        )

    async def recent_cycles(self, limit: int) -> tuple[ExecutionCycleRecord, ...]:
        """Return the newest cycles first."""
        ordered = sorted(self._records.values(), key=lambda record: record.started_at, reverse=True)
        return tuple(ordered[: max(limit, 0)])
