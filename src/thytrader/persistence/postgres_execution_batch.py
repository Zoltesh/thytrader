"""Load many full deployment snapshots with one statement per related table.

``PostgresExecutionStore.get_deployment`` issues six statements per deployment. The
execution worker's entry gate reloads every book's snapshot after each book it processes,
which made that reload the cycle's largest cost (ADR 0131). ``load_snapshots`` reads the
same rows for many deployments with one ``IN`` statement per table and assembles each
snapshot exactly as ``_snapshot`` does: the same fields, the same per-table ordering
(intents and orders newest created first, fills newest filled first, funding by product and
hour) and the same focused-position rule.
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from sqlalchemy import select

from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.persistence.postgres_execution_rows import (
    _deployment_from_row,
    _fill_from_row,
    _intent_from_row,
    _order_from_row,
    _position_from_row,
    _runtime_from_row,
)
from thytrader.persistence.postgres_execution_snapshots import (
    FILL_ORDER,
    INTENT_ORDER,
    ORDER_ORDER,
    POSITION_ORDER,
    RUNTIME_ORDER,
)
from thytrader.persistence.postgres_futures_funding import _flow_from_row
from thytrader.persistence.schema import (
    deployments,
    execution_fills,
    execution_instrument_state,
    execution_orders,
    execution_positions,
    futures_funding_entries,
    order_intents,
)
from thytrader.trading.models import DeploymentSnapshot, ExecutionStoreError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from uuid import UUID

    from sqlalchemy import Table
    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncConnection
    from sqlalchemy.sql.elements import ColumnElement

    from thytrader.trading.models import Deployment, Position


async def load_snapshots(
    connection: AsyncConnection, deployment_ids: Sequence[UUID]
) -> tuple[DeploymentSnapshot, ...]:
    """Return one full snapshot per id, in the given order; fail when any id is missing."""
    if not deployment_ids:
        return ()
    ids = list(dict.fromkeys(deployment_ids))
    rows = (
        (await connection.execute(select(deployments).where(deployments.c.id.in_(ids))))
        .mappings()
        .all()
    )
    loaded = {row["id"]: _deployment_from_row(row) for row in rows}
    if any(deployment_id not in loaded for deployment_id in ids):
        raise ExecutionStoreError("Deployment was not found.")
    intents = await _grouped(connection, order_intents, ids, *INTENT_ORDER)
    orders = await _grouped(connection, execution_orders, ids, *ORDER_ORDER)
    fills = await _grouped(connection, execution_fills, ids, *FILL_ORDER)
    positions = await _grouped(connection, execution_positions, ids, *POSITION_ORDER)
    runtimes = await _grouped(connection, execution_instrument_state, ids, *RUNTIME_ORDER)
    futures_ids = [
        deployment_id
        for deployment_id in ids
        if is_futures_product_id(loaded[deployment_id].product_id)
    ]
    funding = await _grouped(
        connection,
        futures_funding_entries,
        futures_ids,
        futures_funding_entries.c.product_id,
        futures_funding_entries.c.funding_time,
    )
    return tuple(
        _assemble(
            loaded[deployment_id],
            intents=intents,
            orders=orders,
            fills=fills,
            positions=positions,
            runtimes=runtimes,
            funding=funding,
        )
        for deployment_id in deployment_ids
    )


async def _grouped(
    connection: AsyncConnection,
    table: Table,
    ids: Sequence[UUID],
    *order_by: ColumnElement[object],
) -> Mapping[UUID, list[RowMapping]]:
    """Select one table's rows for every id, grouped by deployment in statement order."""
    grouped: defaultdict[UUID, list[RowMapping]] = defaultdict(list)
    if not ids:
        return grouped
    statement = select(table).where(table.c.deployment_id.in_(list(ids)))
    if order_by:
        statement = statement.order_by(*order_by)
    for row in (await connection.execute(statement)).mappings().all():
        grouped[row["deployment_id"]].append(row)
    return grouped


def _assemble(
    deployment: Deployment,
    *,
    intents: Mapping[UUID, list[RowMapping]],
    orders: Mapping[UUID, list[RowMapping]],
    fills: Mapping[UUID, list[RowMapping]],
    positions: Mapping[UUID, list[RowMapping]],
    runtimes: Mapping[UUID, list[RowMapping]],
    funding: Mapping[UUID, list[RowMapping]],
) -> DeploymentSnapshot:
    """Build one snapshot from grouped rows exactly as the single-deployment loader does."""
    key = deployment.id
    loaded_positions = tuple(_position_from_row(row) for row in positions.get(key, ()))
    return DeploymentSnapshot(
        deployment=deployment,
        position=_focused(loaded_positions, deployment),
        orders=tuple(_order_from_row(row) for row in orders.get(key, ())),
        fills=tuple(_fill_from_row(row) for row in fills.get(key, ())),
        intents=tuple(_intent_from_row(row) for row in intents.get(key, ())),
        positions=loaded_positions,
        instrument_runtimes=tuple(_runtime_from_row(row) for row in runtimes.get(key, ())),
        funding=tuple(_flow_from_row(row) for row in funding.get(key, ())),
    )


def _focused(positions: tuple[Position, ...], deployment: Deployment) -> Position | None:
    """Pick the deployment's focused position with ``_snapshot``'s rule."""
    if len(positions) == 1:
        return positions[0]
    return next(
        (item for item in positions if item.product_id in {"", deployment.product_id}),
        None,
    )
