"""Cross-process serialized fleet operations with atomic causal receipts.

A session advisory lock serializes one idempotency key across transactions and
API instances. Each latch/command commits with its receipt; a restart can replay
receipts without inspecting coincidental current state or undoing newer choices.
No venue I/O runs under these locks.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import replace
import hashlib
from typing import TYPE_CHECKING

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.fleet_control.models import FleetAction, FleetOperation
from thytrader.fleet_control.serialization import operation_from_json, operation_to_json
from thytrader.fleet_control.store import modes_for, require_inhibition_revisions
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.schema import fleet_control_operations, fleet_entry_inhibition
from thytrader.trading.entry_latch import InhibitionSnapshot
from thytrader.trading.models import ExecutionStoreError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from datetime import datetime

    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

    from thytrader.fleet_control.models import ExpectedTarget, FleetExecuteRequest
    from thytrader.trading.store import ExecutionStore


class PostgresFleetControlStore:
    """Persist fleet effects and receipts with cross-instance same-key exclusion."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind a per-task guarded connection to the operational engine."""
        self._engine = engine
        self._guarded: ContextVar[AsyncConnection | None] = ContextVar("fleet_guard", default=None)
        self._guarded_key: ContextVar[str | None] = ContextVar("fleet_guard_key", default=None)
        self._local = asyncio.Lock()

    def validate_execution(self, execution: ExecutionStore) -> None:
        """Require the same operational engine for command and receipt transactions."""
        if (
            not isinstance(execution, PostgresExecutionStore)
            or execution.fleet_database_engine is not self._engine
        ):
            raise ExecutionStoreError(
                "Fleet receipts and deployments require the same database engine."
            )

    @asynccontextmanager
    async def operation_guard(self, idempotency_key: str) -> AsyncIterator[None]:
        """Bound local session reservations; the database lock provides cross-instance safety."""
        async with self._local, self._database_guard(idempotency_key):
            yield

    @asynccontextmanager
    async def _database_guard(self, idempotency_key: str) -> AsyncIterator[None]:
        """Hold a database session lock until every committed receipt is recorded.

        Closing a failed/cancelled session releases its lock. Explicit unlock is
        mandatory before a healthy connection returns to the pool.
        """
        digest = hashlib.blake2b(f"fleet:{idempotency_key}".encode(), digest_size=8).digest()
        lock_id = int.from_bytes(digest, "big", signed=True)
        async with self._engine.connect() as connection:
            token = self._guarded.set(connection)
            key_token = self._guarded_key.set(idempotency_key)
            try:
                await connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": lock_id})
                await connection.commit()
                yield
                await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_id})
                await connection.commit()
            except SQLAlchemyError as error:
                await _invalidate_session(connection)
                raise ExecutionStoreError(
                    "Fleet operation serialization unavailable; result unknown."
                ) from error
            except BaseException:
                # Do not return a session holding an advisory lock to the pool.
                await _invalidate_session(connection)
                raise
            finally:
                self._guarded.reset(token)
                self._guarded_key.reset(key_token)

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[AsyncConnection]:
        """Use the guarded session when present, otherwise a standalone transaction."""
        connection = self._guarded.get()
        try:
            if connection is None:
                async with self._engine.begin() as standalone:
                    yield standalone
            else:
                async with connection.begin():
                    yield connection
        except SQLAlchemyError as error:
            raise ExecutionStoreError(
                "Fleet storage is unavailable; result is not accepted."
            ) from error

    async def read_inhibition(self) -> InhibitionSnapshot:
        """Read both mode rows; absence is unknown and fails closed."""
        async with self._transaction() as connection:
            return await _read_snapshot(connection)

    async def inhibit(self, modes: tuple[str, ...], *, now: datetime) -> InhibitionSnapshot:
        """Direct latch write for test scaffolding; API uses receipt-coupled apply_latch."""
        return await self._write(modes, inhibited=True, now=now)

    async def release(self, modes: tuple[str, ...], *, now: datetime) -> InhibitionSnapshot:
        """Direct latch write for test scaffolding; API uses receipt-coupled apply_latch."""
        return await self._write(modes, inhibited=False, now=now)

    async def _write(
        self, modes: tuple[str, ...], *, inhibited: bool, now: datetime
    ) -> InhibitionSnapshot:
        """Update locked modes in a single transaction."""
        async with self._transaction() as connection:
            await _lock_modes(connection, modes)
            await _set_modes(connection, modes, inhibited=inhibited, now=now)
            return await _read_snapshot(connection)

    async def get_operation(self, idempotency_key: str) -> FleetOperation | None:
        """Read the last committed causal receipt, including pending progress."""
        async with self._transaction() as connection:
            value = await connection.scalar(
                select(fleet_control_operations.c.result_json).where(
                    fleet_control_operations.c.idempotency_key == idempotency_key
                )
            )
        return None if value is None else operation_from_json(str(value))

    async def insert_operation(self, operation: FleetOperation) -> bool:
        """Persist an intent before effects; a conflicting key is never overwritten."""
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
        async with self._transaction() as connection:
            return (await connection.execute(statement)).rowcount == 1

    async def save_operation(self, operation: FleetOperation) -> None:
        """Save progress only while the cross-process same-key lock is held."""
        self._require_guard(operation.idempotency_key)
        async with self._transaction() as connection:
            await _save(connection, operation)

    def _require_guard(self, idempotency_key: str) -> None:
        """Forbid an unfenced writer from clobbering another instance's progress."""
        if self._guarded.get() is None or self._guarded_key.get() != idempotency_key:
            raise ExecutionStoreError("Fleet progress write requires its database operation guard.")

    async def apply_latch(
        self, operation: FleetOperation, request: FleetExecuteRequest, *, now: datetime
    ) -> FleetOperation:
        """Commit latch revision and receipt together; replay never changes the latch."""
        self._require_guard(operation.idempotency_key)
        if operation.latch_applied:
            return operation
        modes = modes_for(request.mode.value)
        async with self._transaction() as connection:
            await _lock_modes(connection, modes)
            snapshot = await _read_snapshot(connection)
            require_inhibition_revisions(snapshot, request.expected_inhibition, modes)
            await _set_modes(
                connection, modes, inhibited=request.action is FleetAction.DISARM, now=now
            )
            saved = replace(
                operation,
                inhibition=await _read_snapshot(connection),
                latch_applied=True,
                updated_at=now,
            )
            await _save(connection, saved)
            return saved

    async def record_target(
        self,
        execution: ExecutionStore,
        operation: FleetOperation,
        expected: ExpectedTarget,
        *,
        now: datetime,
    ) -> FleetOperation:
        """Atomically record the real revision-guarded command and its causal receipt."""
        self._require_guard(operation.idempotency_key)
        if (
            not isinstance(execution, PostgresExecutionStore)
            or execution.fleet_database_engine is not self._engine
        ):
            raise ExecutionStoreError(
                "Fleet receipts and deployments require the same database engine."
            )
        async with self._transaction() as connection:
            result = await execution.record_confirmed_fleet_command(
                connection, expected, operation.action, operation.mode, now=now
            )
            saved = replace(operation, targets=(*operation.targets, result), updated_at=now)
            await _save(connection, saved)
            return saved


async def _invalidate_session(connection: AsyncConnection) -> None:
    """Finish invalidation before pool return, even under repeated cancellation."""
    task = asyncio.create_task(connection.invalidate())
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # A second cancellation must not abandon the physical-session cleanup.
            continue
    task.result()


async def _save(connection: AsyncConnection, operation: FleetOperation) -> None:
    """Write a receipt in the caller's transaction without changing request identity."""
    result = await connection.execute(
        fleet_control_operations.update()
        .where(
            fleet_control_operations.c.id == operation.id,
            fleet_control_operations.c.request_fingerprint == operation.request_fingerprint,
        )
        .values(
            status=operation.status.value,
            result_json=operation_to_json(operation),
            updated_at=operation.updated_at,
        )
    )
    if result.rowcount != 1:
        raise ExecutionStoreError("Fleet operation receipt was not found.")


async def _lock_modes(connection: AsyncConnection, modes: tuple[str, ...]) -> None:
    """Lock mode rows in a consistent order, failing closed on absent state."""
    rows = (
        (
            await connection.execute(
                select(fleet_entry_inhibition.c.mode)
                .where(fleet_entry_inhibition.c.mode.in_(modes))
                .order_by(fleet_entry_inhibition.c.mode)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if set(rows) != set(modes):
        raise ExecutionStoreError("Entry inhibition state is incomplete.")


async def _set_modes(
    connection: AsyncConnection, modes: tuple[str, ...], *, inhibited: bool, now: datetime
) -> None:
    """Advance the revision for every deliberate command, even an unchanged bit."""
    await connection.execute(
        fleet_entry_inhibition.update()
        .where(fleet_entry_inhibition.c.mode.in_(modes))
        .values(inhibited=inhibited, revision=fleet_entry_inhibition.c.revision + 1, updated_at=now)
    )


async def _read_snapshot(connection: AsyncConnection) -> InhibitionSnapshot:
    """Read both mode rows in the current transaction."""
    rows = (await connection.execute(select(fleet_entry_inhibition))).mappings().all()
    return _snapshot_from_rows(rows)


def _snapshot_from_rows(rows: Sequence[RowMapping]) -> InhibitionSnapshot:
    """Validate both seeded mode rows; do not infer an omitted mode is clear."""
    by_mode = {str(row["mode"]): row for row in rows}
    paper, live = by_mode.get("paper"), by_mode.get("live")
    if paper is None or live is None:
        raise ExecutionStoreError("Entry inhibition state is incomplete.")
    return InhibitionSnapshot(
        paper_inhibited=bool(paper["inhibited"]),
        live_inhibited=bool(live["inhibited"]),
        paper_revision=int(paper["revision"]),
        live_revision=int(live["revision"]),
        updated_at=max(paper["updated_at"], live["updated_at"]),
    )
