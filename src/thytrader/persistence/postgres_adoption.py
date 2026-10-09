"""PostgreSQL in-kind inventory adoption under the per-base advisory lock (ADR 0124).

One transaction locks the live base exclusively, reads every live book's claims, reads the
venue balance through the caller, and only then writes the intent, the FILLED order, the
applied fill, the position, the product runtime and the book's next revision. When asked,
it inserts the new book first in the same transaction. Any refusal or failure rolls back
every row. Kept out of :mod:`thytrader.persistence.postgres_execution`, which is near its
size budget, and off the execution-store wrappers.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError

from thytrader.persistence.postgres_execution_rows import (
    _deployment_from_row,
    _deployment_values,
    _instrument_runtime_upsert,
    _mutable_deployment_values,
)
from thytrader.persistence.postgres_execution_snapshots import _snapshot
from thytrader.persistence.postgres_execution_statements import (
    _fill_insert,
    _intent_insert,
    _locked_deployment_select,
    _order_upsert,
    _position_insert,
)
from thytrader.persistence.postgres_fleet_admission import refuse_postgres_entry
from thytrader.persistence.postgres_inventory_lock import lock_live_base
from thytrader.persistence.schema import deployments, execution_positions
from thytrader.trading.adoption_write import (
    AdoptionCommit,
    flat_snapshot,
    prepare_adoption,
    read_venue_balances,
)
from thytrader.trading.fill_ledger import fill_projection_deployment
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentSnapshot,
    ExecutionConflictError,
    ExecutionStoreError,
    resolved_product_id,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

    from thytrader.trading.adoption_write import AdoptionWrite, BalanceReader, PreparedAdoption

LOCK_TIMEOUT = "5s"
"""How long an adoption waits for the base lock before refusing as busy."""
BALANCE_READ_TIMEOUT_SECONDS = 10.0
"""How long the venue balance read may hold the base lock."""

_LOCK_TIMEOUT = text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")


class PostgresInventoryAdoptionStore:
    """Commit adoptions in PostgreSQL; implements ``InventoryAdoptionStore``."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind the store to a managed async engine."""
        self._engine = engine

    async def adopt_inventory(
        self, write: AdoptionWrite, *, read_balances: BalanceReader
    ) -> AdoptionCommit:
        """Lock, read claims, read the venue, then write everything or nothing."""
        try:
            async with self._engine.begin() as connection:
                await connection.execute(_LOCK_TIMEOUT)
                await lock_live_base(connection, write.base, shared=False)
                live_books = await _live_snapshots(connection)
                balances = await read_venue_balances(
                    read_balances, timeout_seconds=BALANCE_READ_TIMEOUT_SECONDS
                )
                book = await _target_book(connection, write)
                prepared = prepare_adoption(
                    write, book=book, live_books=live_books, balances=balances
                )
                snapshot = await _write(connection, book, prepared)
                return AdoptionCommit(
                    snapshot=snapshot,
                    records=prepared.records,
                    availability=prepared.availability,
                )
        except IntegrityError as error:
            raise ExecutionConflictError(_integrity_detail(error)) from error
        except DBAPIError as error:
            if "lock timeout" in str(error).lower():
                raise ExecutionConflictError(
                    f"Another adoption holds the {write.base} inventory lock; retry shortly."
                ) from error
            raise ExecutionStoreError("Execution storage is unavailable.") from error
        except SQLAlchemyError as error:
            raise ExecutionStoreError("Execution storage is unavailable.") from error


async def _live_snapshots(connection: AsyncConnection) -> tuple[DeploymentSnapshot, ...]:
    """Every live book, including stopped ones, read inside the locked transaction."""
    rows = (
        (
            await connection.execute(
                select(deployments)
                .where(deployments.c.mode == DeploymentMode.LIVE.value)
                .order_by(deployments.c.id)
            )
        )
        .mappings()
        .all()
    )
    return tuple([await _snapshot(connection, _deployment_from_row(row)) for row in rows])


async def _target_book(connection: AsyncConnection, write: AdoptionWrite) -> DeploymentSnapshot:
    """Insert the new book (latch-checked when asked), or row-lock the existing one."""
    created = write.new_deployment
    if created is not None:
        if created.id != write.deployment_id:
            raise ExecutionConflictError("The new book is not the adoption's book.")
        if write.respect_entry_latch:
            await refuse_postgres_entry(connection, mode=created.mode.value, action="start")
        await connection.execute(insert(deployments).values(_deployment_values(created)))
        row = (await connection.execute(_locked_deployment_select(created.id))).mappings().one()
        return flat_snapshot(_deployment_from_row(row))
    row = (
        (await connection.execute(_locked_deployment_select(write.deployment_id)))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ExecutionStoreError("Deployment was not found.")
    deployment = _deployment_from_row(row)
    if write.expected_revision is not None and deployment.revision != write.expected_revision:
        raise ExecutionConflictError("Deployment revision conflict.")
    return await _snapshot(connection, deployment)


async def _write(
    connection: AsyncConnection, book: DeploymentSnapshot, prepared: PreparedAdoption
) -> DeploymentSnapshot:
    """Write the records, runtime, position and next revision; return the reloaded book."""
    deployment = book.deployment
    records = prepared.records
    projected = prepared.projected
    await connection.execute(_intent_insert(records.intent))
    await connection.execute(_order_upsert(records.order))
    await connection.execute(
        _fill_insert(prepared.fill, economics_applied_at=prepared.fill.economics_applied_at)
    )
    for runtime in projected.instrument_runtimes:
        await connection.execute(_instrument_runtime_upsert(runtime, deployment.id))
    parent = fill_projection_deployment(book, projected)
    next_revision = deployment.revision + 1
    values = _mutable_deployment_values(parent)
    values["revision"] = next_revision
    updated = await connection.execute(
        deployments.update()
        .where(deployments.c.id == deployment.id, deployments.c.revision == deployment.revision)
        .values(values)
    )
    if updated.rowcount != 1:
        raise ExecutionConflictError("Deployment revision conflict.")
    product_id = resolved_product_id(records.order.product_id, deployment)
    await connection.execute(
        delete(execution_positions).where(
            execution_positions.c.deployment_id == deployment.id,
            execution_positions.c.product_id == product_id,
        )
    )
    position = projected.position
    if position is not None:
        await connection.execute(
            _position_insert(position, stamped_product=position.product_id or product_id)
        )
    return await _snapshot(connection, replace(parent, revision=next_revision))


def _integrity_detail(error: IntegrityError) -> str:
    """Name the duplicate that refused the adoption."""
    text_value = str(error).lower()
    if "idempotency" in text_value:
        return "idempotency_key already used"
    if "ux_deployments_active_strategy_mode" in text_value:
        return "A running or paused deployment already exists for this strategy and mode."
    return "The adoption conflicts with an existing record."
