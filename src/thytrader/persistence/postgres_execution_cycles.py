"""PostgreSQL repository for execution-worker cycle timing records (ADR 0131)."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from pydantic import ValidationError
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.observability.execution_cycle import ExecutionCycleRecord, ExecutionCycleReport
from thytrader.persistence.execution_cycles import (
    MAX_RECENT_CYCLES,
    ExecutionCycleStoreUnavailableError,
)
from thytrader.persistence.schema import execution_cycles

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.engine import Row
    from sqlalchemy.ext.asyncio import AsyncEngine

RETENTION = timedelta(days=1)


class PostgresExecutionCycleStore:
    """Insert each cycle at start, complete it with its report, keep one day of cycles."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind cycle operations to one managed async engine."""
        self._engine = engine

    async def start_cycle(
        self, cycle_id: UUID, started_at: datetime, interval_seconds: int
    ) -> None:
        """Insert one started cycle and prune cycles older than the retention window."""
        statement = (
            insert(execution_cycles)
            .values(
                cycle_id=cycle_id,
                started_at=started_at,
                interval_seconds=max(interval_seconds, 1),
            )
            .on_conflict_do_nothing(index_elements=["cycle_id"])
        )
        prune = delete(execution_cycles).where(
            execution_cycles.c.started_at < started_at - RETENTION
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
                await connection.execute(prune)
        except SQLAlchemyError as error:
            raise ExecutionCycleStoreUnavailableError(
                "Execution cycle storage is unavailable."
            ) from error

    async def complete_cycle(self, report: ExecutionCycleReport) -> None:
        """Attach one report to its cycle, inserting the row when its start was not stored."""
        values = {
            "completed_at": report.completed_at,
            "duration_seconds": report.duration_seconds,
            "report_json": report.model_dump_json(),
        }
        statement = (
            insert(execution_cycles)
            .values(
                cycle_id=report.cycle_id,
                started_at=report.started_at,
                interval_seconds=report.interval_seconds,
                **values,
            )
            .on_conflict_do_update(index_elements=["cycle_id"], set_=values)
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise ExecutionCycleStoreUnavailableError(
                "Execution cycle storage is unavailable."
            ) from error

    async def recent_cycles(self, limit: int) -> tuple[ExecutionCycleRecord, ...]:
        """Return up to ``limit`` cycles (at most 200), newest start first."""
        bounded = min(max(limit, 0), MAX_RECENT_CYCLES)
        statement = (
            select(execution_cycles)
            .order_by(execution_cycles.c.started_at.desc(), execution_cycles.c.cycle_id.desc())
            .limit(bounded)
        )
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).all()
        except SQLAlchemyError as error:
            raise ExecutionCycleStoreUnavailableError(
                "Execution cycle storage is unavailable."
            ) from error
        return tuple(_to_record(row) for row in rows)


def _to_record(row: Row[tuple[object, ...]]) -> ExecutionCycleRecord:
    """Map one row; a report that no longer validates is dropped, not trusted."""
    report = None
    if isinstance(row.report_json, str):
        try:
            report = ExecutionCycleReport.model_validate_json(row.report_json)
        except ValidationError:
            report = None
    return ExecutionCycleRecord(
        cycle_id=row.cycle_id,
        started_at=row.started_at,
        interval_seconds=row.interval_seconds,
        completed_at=row.completed_at if report is not None else None,
        report=report,
    )


__all__ = ["RETENTION", "PostgresExecutionCycleStore"]
