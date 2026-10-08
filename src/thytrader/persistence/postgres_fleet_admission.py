"""PostgreSQL row-locked fleet entry-inhibition check shared by deployment and intent writes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from thytrader.fleet_control import ENTRY_INHIBITED_PREFIX
from thytrader.fleet_control.admission import entry_inhibited_detail
from thytrader.persistence.schema import fleet_entry_inhibition
from thytrader.trading.models import ExecutionConflictError, ExecutionStoreError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection


async def refuse_postgres_entry(connection: AsyncConnection, *, mode: str, action: str) -> None:
    """Lock one latch row and refuse when that mode is inhibited.

    The row lock is held by the caller's transaction, so a disarm cannot commit
    between this check and the deployment or intent insert.
    """
    try:
        async with connection.begin_nested():
            inhibited = (
                await connection.execute(
                    select(fleet_entry_inhibition.c.inhibited)
                    .where(fleet_entry_inhibition.c.mode == mode)
                    .with_for_update()
                )
            ).scalar_one_or_none()
    except SQLAlchemyError as error:
        raise ExecutionStoreError("Entry inhibition storage is unavailable.") from error
    if inhibited is None:
        raise ExecutionConflictError(
            f"{ENTRY_INHIBITED_PREFIX}: {mode} entry inhibition state is missing; "
            "refusing the risk-increasing action."
        )
    if inhibited:
        raise ExecutionConflictError(entry_inhibited_detail(mode, action=action))
