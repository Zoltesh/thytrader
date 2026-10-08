"""Durable entry-inhibition checks shared by starts and entry intents.

Exits, protection, and cancellations do not call these helpers. Missing tables,
rows, or unreadable state fail closed for risk-increasing actions only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from thytrader.fleet_control import ENTRY_INHIBITED_PREFIX
from thytrader.persistence.schema import fleet_entry_inhibition
from thytrader.trading.entry_latch import remember_entry_inhibition
from thytrader.trading.models import ExecutionConflictError, ExecutionStoreError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection


def entry_inhibited_detail(mode: str, *, action: str) -> str:
    """Stable conflict text for a refused start or entry."""
    noun = "starts" if action == "start" else "entries"
    return (
        f"{ENTRY_INHIBITED_PREFIX}: New {mode} {noun} are refused until an explicit "
        "fleet rearm. Disarm does not flatten, cancel, or pause residual positions."
    )


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


def remember_snapshot(*, paper: bool, live: bool) -> None:
    """Publish a latch snapshot for the synchronous entry gate in this process."""
    remember_entry_inhibition({"paper": paper, "live": live})


async def refresh_process_entry_inhibition(store: object) -> None:
    """Load the latch into this process before a worker cycle admits entries.

    An absent reader or failed read inhibits entries only; the caller still
    runs exits, protection, and reconciliation.
    """
    reader = getattr(store, "read_entry_inhibition", None)
    if not callable(reader):
        remember_entry_inhibition({"paper": True, "live": True})
        return
    try:
        snapshot = await reader()
    except ExecutionStoreError:
        remember_entry_inhibition({"paper": True, "live": True})
        return
    if (
        not isinstance(snapshot, dict)
        or not isinstance(snapshot.get("paper"), bool)
        or not isinstance(snapshot.get("live"), bool)
    ):
        remember_entry_inhibition({"paper": True, "live": True})
        return
    remember_entry_inhibition({"paper": snapshot["paper"], "live": snapshot["live"]})
