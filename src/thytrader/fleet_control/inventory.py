"""Stable deployment inventory reads.

``updated_at`` moves whenever a book is supervised, so offset pages ordered by
it skip or repeat rows. Inventory pages order by immutable ``created_at`` then
``id``. Callers pin ``as_of`` from the first page so a deployment created during
the walk cannot shift later offsets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from thytrader.execution.models import ExecutionStoreError
from thytrader.fleet_control.models import INVENTORY_ORDER

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.execution.models import Deployment
    from thytrader.execution.store import ExecutionStore


@dataclass(frozen=True, slots=True)
class InventoryPage:
    """One stable inventory page plus the snapshot the next page must pin."""

    deployments: tuple[Deployment, ...]
    limit: int
    offset: int
    returned: int
    total: int
    has_more: bool
    as_of: datetime
    order: str = INVENTORY_ORDER


async def read_stable_inventory(
    store: ExecutionStore,
    *,
    limit: int,
    offset: int,
    strategy_id: UUID | None = None,
    as_of: datetime | None = None,
) -> InventoryPage:
    """Return one stable page, using a store method when the backend has one."""
    if limit < 1:
        raise ExecutionStoreError("Inventory page limit must be positive.")
    if offset < 0:
        raise ExecutionStoreError("Inventory offset must be zero or positive.")
    native = getattr(store, "list_stable_inventory", None)
    snapshot_as_of = datetime.now(UTC) if as_of is None else as_of
    if native is not None:
        return await native(
            limit=limit,
            offset=offset,
            strategy_id=strategy_id,
            as_of=snapshot_as_of,
        )
    rows = await store.list_deployments()
    return page_deployments(
        rows,
        limit=limit,
        offset=offset,
        strategy_id=strategy_id,
        as_of=snapshot_as_of,
    )


def page_deployments(
    rows: tuple[Deployment, ...] | list[Deployment],
    *,
    limit: int,
    offset: int,
    strategy_id: UUID | None,
    as_of: datetime,
) -> InventoryPage:
    """Page an already loaded inventory by the stable created-at contract."""
    selected = [row for row in rows if _matches(row, strategy_id=strategy_id, as_of=as_of)]
    selected.sort(key=lambda row: (row.created_at, str(row.id)), reverse=True)
    page = tuple(selected[offset : offset + limit])
    total = len(selected)
    return InventoryPage(
        deployments=page,
        limit=limit,
        offset=offset,
        returned=len(page),
        total=total,
        has_more=offset + len(page) < total,
        as_of=as_of,
    )


def _matches(row: Deployment, *, strategy_id: UUID | None, as_of: datetime) -> bool:
    """Keep rows inside the pinned snapshot and optional strategy filter."""
    if strategy_id is not None and row.strategy_id != strategy_id:
        return False
    return row.created_at <= as_of
