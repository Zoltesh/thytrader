"""PostgreSQL repository for supervised worker heartbeats."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.persistence.schema import worker_heartbeats
from thytrader.persistence.worker_heartbeats import WorkerHeartbeatUnavailableError, WorkerName

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncEngine


class PostgresWorkerHeartbeatStore:
    """Transactional latest-heartbeat repository shared by workers and operator health."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind heartbeat operations to one managed async engine."""
        self._engine = engine

    async def touch(self, worker_name: WorkerName, at: datetime) -> None:
        """Insert or replace one worker's latest heartbeat."""
        statement = insert(worker_heartbeats).values(
            worker_name=worker_name,
            heartbeat_at=at,
        )
        statement = statement.on_conflict_do_update(
            index_elements=["worker_name"],
            set_={"heartbeat_at": at},
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise WorkerHeartbeatUnavailableError(
                "Worker heartbeat storage is unavailable."
            ) from error

    async def last_heartbeat(self, worker_name: WorkerName) -> datetime | None:
        """Return the latest heartbeat instant when one has been recorded."""
        statement = select(worker_heartbeats.c.heartbeat_at).where(
            worker_heartbeats.c.worker_name == worker_name
        )
        try:
            async with self._engine.connect() as connection:
                value = (await connection.execute(statement)).scalar_one_or_none()
        except SQLAlchemyError as error:
            raise WorkerHeartbeatUnavailableError(
                "Worker heartbeat storage is unavailable."
            ) from error
        return cast("datetime | None", value)
