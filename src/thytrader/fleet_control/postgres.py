"""PostgreSQL fleet latch and operation log.

Latch updates lock ``fleet_entry_inhibition`` rows. Deployment inserts lock the
same rows, so a start cannot commit after a disarm that was already waiting.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.execution.models import ExecutionStoreError
from thytrader.fleet_control.models import FleetOperation, InhibitionSnapshot
from thytrader.fleet_control.serialization import operation_from_json, operation_to_json
from thytrader.persistence.schema import fleet_control_operations, fleet_entry_inhibition

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine


class PostgresFleetControlStore:
    """Persist the fleet latch and operation log in PostgreSQL."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind the store to the shared operational engine."""
        self._engine = engine
        self._guards: dict[str, asyncio.Lock] = {}

    def operation_guard(self, idempotency_key: str) -> asyncio.Lock:
        """Serialize one key inside this process. The unique index covers others."""
        return self._guards.setdefault(idempotency_key, asyncio.Lock())

    async def read_inhibition(self) -> InhibitionSnapshot:
        """Read both mode rows. A missing row is a storage failure."""
        return await self._read()

    async def inhibit(self, modes: tuple[str, ...], *, now: datetime) -> InhibitionSnapshot:
        """Set inhibition under row locks."""
        return await self._write(modes, inhibited=True, now=now)

    async def release(self, modes: tuple[str, ...], *, now: datetime) -> InhibitionSnapshot:
        """Clear inhibition under row locks."""
        return await self._write(modes, inhibited=False, now=now)

    async def get_operation(self, idempotency_key: str) -> FleetOperation | None:
        """Load one operation by its idempotency key."""
        try:
            async with self._engine.connect() as connection:
                row = (
                    (
                        await connection.execute(
                            select(fleet_control_operations.c.result_json).where(
                                fleet_control_operations.c.idempotency_key == idempotency_key
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Fleet operation storage is unavailable.") from error
        if row is None:
            return None
        return operation_from_json(str(row["result_json"]))

    async def insert_operation(self, operation: FleetOperation) -> bool:
        """Insert a new key. False when another request already owns it."""
        payload = operation_to_json(operation)
        statement = (
            insert(fleet_control_operations)
            .values(
                id=operation.id,
                idempotency_key=operation.idempotency_key,
                action=operation.action.value,
                mode=operation.mode.value,
                status=operation.status.value,
                request_fingerprint=operation.request_fingerprint,
                request_json=payload,
                result_json=payload,
                created_at=operation.created_at,
                updated_at=operation.updated_at,
            )
            .on_conflict_do_nothing(index_elements=[fleet_control_operations.c.idempotency_key])
        )
        try:
            async with self._engine.begin() as connection:
                result = await connection.execute(statement)
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Fleet operation storage is unavailable.") from error
        return result.rowcount == 1

    async def save_operation(self, operation: FleetOperation) -> None:
        """Replace the stored result for one key."""
        statement = (
            fleet_control_operations.update()
            .where(fleet_control_operations.c.idempotency_key == operation.idempotency_key)
            .values(
                status=operation.status.value,
                result_json=operation_to_json(operation),
                updated_at=operation.updated_at,
            )
        )
        try:
            async with self._engine.begin() as connection:
                result = await connection.execute(statement)
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Fleet operation storage is unavailable.") from error
        if result.rowcount != 1:
            raise ExecutionStoreError("Fleet operation was not found.")

    async def _write(
        self, modes: tuple[str, ...], *, inhibited: bool, now: datetime
    ) -> InhibitionSnapshot:
        """Update selected modes while holding their row locks."""
        try:
            async with self._engine.begin() as connection:
                await _lock_modes(connection, modes, inhibited=inhibited, now=now)
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Entry inhibition storage is unavailable.") from error
        return await self._read()

    async def _read(self) -> InhibitionSnapshot:
        """Read both seeded mode rows."""
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(select(fleet_entry_inhibition))).mappings().all()
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Entry inhibition storage is unavailable.") from error
        return _snapshot_from_rows(rows)


async def _lock_modes(
    connection: AsyncConnection,
    modes: tuple[str, ...],
    *,
    inhibited: bool,
    now: datetime,
) -> None:
    """Lock and update the requested mode rows inside the caller's transaction."""
    rows = (
        (
            await connection.execute(
                select(fleet_entry_inhibition)
                .where(fleet_entry_inhibition.c.mode.in_(modes))
                .with_for_update()
            )
        )
        .mappings()
        .all()
    )
    found = {str(row["mode"]) for row in rows}
    if found != set(modes):
        raise ExecutionStoreError("Entry inhibition state is incomplete.")
    for row in rows:
        if bool(row["inhibited"]) is inhibited:
            continue
        await connection.execute(
            fleet_entry_inhibition.update()
            .where(fleet_entry_inhibition.c.mode == row["mode"])
            .values(
                inhibited=inhibited,
                revision=int(row["revision"]) + 1,
                updated_at=now,
            )
        )


def _snapshot_from_rows(rows: Sequence[RowMapping]) -> InhibitionSnapshot:
    """Build a latch snapshot from both mode rows."""
    by_mode = {str(row["mode"]): row for row in rows}
    paper = by_mode.get("paper")
    live = by_mode.get("live")
    if paper is None or live is None:
        raise ExecutionStoreError("Entry inhibition state is incomplete.")
    paper_updated = paper["updated_at"]
    live_updated = live["updated_at"]
    return InhibitionSnapshot(
        paper_inhibited=bool(paper["inhibited"]),
        live_inhibited=bool(live["inhibited"]),
        paper_revision=int(paper["revision"]),
        live_revision=int(live["revision"]),
        updated_at=paper_updated if paper_updated >= live_updated else live_updated,
    )
