"""PostgreSQL lease queue the research worker pool claims from (ADR 0092).

Two durable tables feed one pool: ``research_jobs`` (backtests and studies) and
``portfolio_backtest_jobs``. A claim is one statement::

    UPDATE <table> SET status = 'running', lease_owner = :owner,
           lease_expires_at = now() + :lease, attempts = attempts + 1
     WHERE job_id = (SELECT job_id FROM <table>
                      WHERE status = 'queued' AND expires_at > now()
                      ORDER BY created_at, job_id
                      LIMIT 1 FOR UPDATE SKIP LOCKED)
    RETURNING job_id, attempts[, kind]

so concurrent workers never claim the same row and never block on each other. A running
row whose lease expired (or that never had one) belongs to a dead worker: a sweep
re-queues it, cancels it when cancellation was requested, or fails it with
``research_worker_lost`` once ``attempts`` reached the configured maximum.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, cast

from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.persistence.schema import portfolio_backtest_jobs, research_jobs, research_workers
from thytrader.research.jobs import ResearchJobErrorCode, ResearchJobStatus
from thytrader.research.worker_pool import (
    RESEARCH_QUEUES,
    WORKER_LOST_MESSAGE,
    ClaimedResearchJob,
    RequeueCounts,
    ResearchQueueDepth,
    ResearchQueueName,
    ResearchWorkerPoolSnapshot,
    ResearchWorkerSlot,
    ResearchWorkerState,
    ResearchWorkKind,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from sqlalchemy import ColumnElement, Table
    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

_TABLES: Final[dict[ResearchQueueName, Table]] = {
    "research_jobs": research_jobs,
    "portfolio_backtest_jobs": portfolio_backtest_jobs,
}
_QUEUED: Final = ResearchJobStatus.QUEUED.value
_RUNNING: Final = ResearchJobStatus.RUNNING.value
_UNAVAILABLE: Final = "Research job queue is unavailable."


class ResearchQueueUnavailableError(RuntimeError):
    """Signal that the research job tables are unreachable."""


class PostgresResearchQueue:
    """Claim, renew, release, and sweep leases on both research job tables."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind the queue to one managed async engine."""
        self._engine = engine

    async def claim(
        self,
        owner: str,
        *,
        lease_seconds: float,
        order: tuple[ResearchQueueName, ...] = RESEARCH_QUEUES,
    ) -> ClaimedResearchJob | None:
        """Claim the oldest queued row of the first queue in ``order`` that has one."""
        for queue in order:
            claimed = await self._claim_from(queue, owner, lease_seconds)
            if claimed is not None:
                return claimed
        return None

    async def renew(self, owner: str, *, lease_seconds: float) -> int:
        """Extend the lease of every running row ``owner`` holds; return how many."""
        renewed = 0
        try:
            async with self._engine.begin() as connection:
                for table in _TABLES.values():
                    result = await connection.execute(
                        update(table)
                        .where(table.c.lease_owner == owner, table.c.status == _RUNNING)
                        .values(lease_expires_at=func.now() + timedelta(seconds=lease_seconds))
                    )
                    renewed += int(result.rowcount or 0)
        except SQLAlchemyError as error:
            raise ResearchQueueUnavailableError(_UNAVAILABLE) from error
        return renewed

    async def release(self, owner: str) -> int:
        """Hand ``owner``'s running rows back to the queue (graceful worker shutdown).

        The attempt is not counted against the job: ``attempts`` is decremented, and a
        row whose cancellation was requested ends cancelled instead of re-queued.
        """
        released = 0
        now = datetime.now(UTC)
        try:
            async with self._engine.begin() as connection:
                for queue, table in _TABLES.items():
                    status = (
                        case(
                            (table.c.cancel_requested.is_(True), ResearchJobStatus.CANCELLED.value),
                            else_=_QUEUED,
                        )
                        if queue == "research_jobs"
                        else _QUEUED
                    )
                    result = await connection.execute(
                        update(table)
                        .where(table.c.lease_owner == owner, table.c.status == _RUNNING)
                        .values(
                            status=status,
                            attempts=func.greatest(table.c.attempts - 1, 0),
                            progress_current=0,
                            lease_owner=None,
                            lease_expires_at=None,
                            updated_at=now,
                        )
                    )
                    released += int(result.rowcount or 0)
        except SQLAlchemyError as error:
            raise ResearchQueueUnavailableError(_UNAVAILABLE) from error
        return released

    async def requeue_expired(
        self,
        *,
        max_attempts: int,
        queues: tuple[ResearchQueueName, ...] = RESEARCH_QUEUES,
    ) -> RequeueCounts:
        """Sweep running rows whose lease expired, or that never had a lease."""
        return await self._sweep(_lease_expired, max_attempts=max_attempts, queues=queues)

    async def requeue_lineage(self, lineage: str, *, max_attempts: int) -> RequeueCounts:
        """Sweep running rows held by a dead predecessor in one supervisor slot."""

        def held_by_lineage(table: Table) -> ColumnElement[bool]:
            """Match running rows whose owner token starts with the lineage."""
            return and_(
                table.c.status == _RUNNING,
                table.c.lease_owner.startswith(lineage, autoescape=True),
            )

        return await self._sweep(held_by_lineage, max_attempts=max_attempts, queues=RESEARCH_QUEUES)

    async def holds_lease(self, queue: ResearchQueueName, job_id: UUID, owner: str) -> bool:
        """Return whether ``owner`` still holds the running row ``job_id``."""
        table = _TABLES[queue]
        statement = select(table.c.job_id).where(
            table.c.job_id == job_id,
            table.c.lease_owner == owner,
            table.c.status == _RUNNING,
        )
        try:
            async with self._engine.connect() as connection:
                row = (await connection.execute(statement)).one_or_none()
        except SQLAlchemyError as error:
            raise ResearchQueueUnavailableError(_UNAVAILABLE) from error
        return row is not None

    async def clear_lease(self, queue: ResearchQueueName, job_id: UUID, owner: str) -> None:
        """Drop ``owner``'s lease columns from a row it finished (any status)."""
        table = _TABLES[queue]
        statement = (
            update(table)
            .where(table.c.job_id == job_id, table.c.lease_owner == owner)
            .values(lease_owner=None, lease_expires_at=None)
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise ResearchQueueUnavailableError(_UNAVAILABLE) from error

    async def record_slot(self, slot: ResearchWorkerSlot) -> None:
        """Insert or replace one worker slot's self-report."""
        values = slot.model_dump(mode="python")
        statement = insert(research_workers).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["slot"],
            set_={key: value for key, value in values.items() if key != "slot"},
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise ResearchQueueUnavailableError(_UNAVAILABLE) from error

    async def prune_slots(self, pool_size: int) -> int:
        """Delete slot rows beyond the configured pool (after the pool shrank)."""
        statement = delete(research_workers).where(research_workers.c.slot >= pool_size)
        try:
            async with self._engine.begin() as connection:
                result = await connection.execute(statement)
        except SQLAlchemyError as error:
            raise ResearchQueueUnavailableError(_UNAVAILABLE) from error
        return int(result.rowcount or 0)

    async def snapshot(self) -> ResearchWorkerPoolSnapshot:
        """Read both queues' depth and every worker slot for health reports."""
        try:
            async with self._engine.connect() as connection:
                research = await _depth(connection, research_jobs)
                portfolio = await _depth(connection, portfolio_backtest_jobs)
                rows = (
                    (
                        await connection.execute(
                            select(research_workers).order_by(research_workers.c.slot)
                        )
                    )
                    .mappings()
                    .all()
                )
        except SQLAlchemyError as error:
            raise ResearchQueueUnavailableError(_UNAVAILABLE) from error
        return ResearchWorkerPoolSnapshot(
            research_jobs=research,
            portfolio_backtests=portfolio,
            workers=tuple(_slot_from_row(row) for row in rows),
        )

    async def _claim_from(
        self, queue: ResearchQueueName, owner: str, lease_seconds: float
    ) -> ClaimedResearchJob | None:
        """Claim one queued row of one table, skipping rows other workers locked."""
        table = _TABLES[queue]
        candidate = (
            select(table.c.job_id)
            .where(table.c.status == _QUEUED, table.c.expires_at > func.now())
            .order_by(table.c.created_at.asc(), table.c.job_id.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        returning = [table.c.job_id, table.c.attempts]
        if queue == "research_jobs":
            returning.append(table.c.kind)
        statement = (
            update(table)
            .where(table.c.job_id == candidate)
            .values(
                status=_RUNNING,
                lease_owner=owner,
                lease_expires_at=func.now() + timedelta(seconds=lease_seconds),
                attempts=table.c.attempts + 1,
                updated_at=datetime.now(UTC),
            )
            .returning(*returning)
        )
        try:
            async with self._engine.begin() as connection:
                row = (await connection.execute(statement)).one_or_none()
        except SQLAlchemyError as error:
            raise ResearchQueueUnavailableError(_UNAVAILABLE) from error
        if row is None:
            return None
        kind: ResearchWorkKind = (
            "portfolio_backtest" if queue == "portfolio_backtest_jobs" else _job_kind(row[2])
        )
        return ClaimedResearchJob(
            queue=queue,
            job_id=cast("UUID", row[0]),
            kind=kind,
            attempts=int(row[1]),
        )

    async def _sweep(
        self,
        orphaned: Callable[[Table], ColumnElement[bool]],
        *,
        max_attempts: int,
        queues: tuple[ResearchQueueName, ...],
    ) -> RequeueCounts:
        """Cancel, fail, or re-queue every orphaned running row of the named tables."""
        counts = RequeueCounts()
        try:
            async with self._engine.begin() as connection:
                for queue in queues:
                    table = _TABLES[queue]
                    counts += await _sweep_table(
                        connection, queue, table, orphaned(table), max_attempts
                    )
        except SQLAlchemyError as error:
            raise ResearchQueueUnavailableError(_UNAVAILABLE) from error
        return counts


def _lease_expired(table: Table) -> ColumnElement[bool]:
    """Match running rows whose lease is gone or past due."""
    return and_(
        table.c.status == _RUNNING,
        or_(table.c.lease_expires_at.is_(None), table.c.lease_expires_at < func.now()),
    )


async def _sweep_table(
    connection: AsyncConnection,
    queue: ResearchQueueName,
    table: Table,
    orphaned: ColumnElement[bool],
    max_attempts: int,
) -> RequeueCounts:
    """Apply cancel, then attempt-limit failure, then re-queue to one table's orphans.

    Each statement re-reads ``status = 'running'``, so a row handled by an earlier
    statement is not touched again by a later one.
    """
    cleared: dict[str, object] = {
        "lease_owner": None,
        "lease_expires_at": None,
        "updated_at": datetime.now(UTC),
    }
    cancelled = 0
    failure: dict[str, object] = {
        "status": ResearchJobStatus.FAILED.value,
        "error_message": WORKER_LOST_MESSAGE[:256],
        **cleared,
    }
    if queue == "research_jobs":
        result = await connection.execute(
            update(table)
            .where(orphaned, table.c.cancel_requested.is_(True))
            .values(status=ResearchJobStatus.CANCELLED.value, **cleared)
        )
        cancelled = int(result.rowcount or 0)
        failure["error_code"] = ResearchJobErrorCode.RESEARCH_WORKER_LOST.value
    failed = await connection.execute(
        update(table).where(orphaned, table.c.attempts >= max_attempts).values(**failure)
    )
    requeued = await connection.execute(
        update(table).where(orphaned).values(status=_QUEUED, progress_current=0, **cleared)
    )
    return RequeueCounts(
        requeued=int(requeued.rowcount or 0),
        cancelled=cancelled,
        failed=int(failed.rowcount or 0),
    )


async def _depth(connection: AsyncConnection, table: Table) -> ResearchQueueDepth:
    """Count queued and running rows and find the oldest queued creation time."""
    statement = select(
        func.count().filter(table.c.status == _QUEUED),
        func.count().filter(table.c.status == _RUNNING),
        func.min(table.c.created_at).filter(table.c.status == _QUEUED),
    ).where(table.c.status.in_([_QUEUED, _RUNNING]))
    row = (await connection.execute(statement)).one()
    return ResearchQueueDepth(
        queued=int(row[0]),
        running=int(row[1]),
        oldest_queued_at=cast("datetime | None", row[2]),
    )


def _job_kind(value: object) -> ResearchWorkKind:
    """Narrow a stored ``research_jobs.kind`` to the claimable work kinds."""
    if value == "backtest":
        return "backtest"
    if value == "study":
        return "study"
    message = f"Research job kind {value!r} is not claimable."
    raise ResearchQueueUnavailableError(message)


def _worker_state(value: object) -> ResearchWorkerState:
    """Narrow a stored slot state; the CHECK constraint admits only these."""
    if value in {"starting", "idle", "running", "stopping"}:
        return cast("ResearchWorkerState", value)
    message = f"Research worker state {value!r} is not recognized."
    raise ResearchQueueUnavailableError(message)


def _optional_kind(value: object) -> ResearchWorkKind | None:
    """Narrow an optional stored slot job kind."""
    if value is None:
        return None
    if value == "portfolio_backtest":
        return "portfolio_backtest"
    return _job_kind(value)


def _slot_from_row(row: RowMapping) -> ResearchWorkerSlot:
    """Map one ``research_workers`` row into a typed slot report."""
    return ResearchWorkerSlot(
        slot=cast("int", row["slot"]),
        pool_size=cast("int", row["pool_size"]),
        pid=cast("int", row["pid"]),
        state=_worker_state(row["state"]),
        job_id=cast("UUID | None", row["job_id"]),
        job_kind=_optional_kind(row["job_kind"]),
        jobs_completed=cast("int", row["jobs_completed"]),
        rss_bytes=cast("int | None", row["rss_bytes"]),
        started_at=cast("datetime", row["started_at"]),
        heartbeat_at=cast("datetime", row["heartbeat_at"]),
    )
