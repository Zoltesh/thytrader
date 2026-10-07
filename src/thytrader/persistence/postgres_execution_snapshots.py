"""Snapshot assembly for the PostgreSQL execution store.

Loads one already-fetched deployment's related rows on the caller's connection (and so
inside the caller's transaction) into a full :class:`DeploymentSnapshot` or a bounded
:class:`DeploymentSummarySnapshot`. Used by :mod:`thytrader.persistence.postgres_execution`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from thytrader.execution.models import (
    DeploymentBookTotals,
    DeploymentSnapshot,
    DeploymentSummarySnapshot,
    OrderStatus,
)
from thytrader.persistence.postgres_execution_rows import (
    _fill_from_row,
    _intent_from_row,
    _order_from_row,
    _position_from_row,
    _runtime_from_row,
)
from thytrader.persistence.schema import (
    execution_fills,
    execution_instrument_state,
    execution_orders,
    execution_positions,
    order_intents,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

    from thytrader.execution.models import Deployment


async def _snapshot(connection: AsyncConnection, deployment: Deployment) -> DeploymentSnapshot:
    """Load related records for one already-fetched deployment."""
    intent_rows = (
        (
            await connection.execute(
                select(order_intents)
                .where(order_intents.c.deployment_id == deployment.id)
                .order_by(order_intents.c.created_at.desc())
            )
        )
        .mappings()
        .all()
    )
    order_rows = (
        (
            await connection.execute(
                select(execution_orders)
                .where(execution_orders.c.deployment_id == deployment.id)
                .order_by(execution_orders.c.created_at.desc())
            )
        )
        .mappings()
        .all()
    )
    fill_rows = (
        (
            await connection.execute(
                select(execution_fills)
                .where(execution_fills.c.deployment_id == deployment.id)
                .order_by(execution_fills.c.filled_at.desc())
            )
        )
        .mappings()
        .all()
    )
    position_rows = (
        (
            await connection.execute(
                select(execution_positions).where(
                    execution_positions.c.deployment_id == deployment.id
                )
            )
        )
        .mappings()
        .all()
    )
    runtime_rows = (
        (
            await connection.execute(
                select(execution_instrument_state).where(
                    execution_instrument_state.c.deployment_id == deployment.id
                )
            )
        )
        .mappings()
        .all()
    )
    positions = tuple(_position_from_row(row) for row in position_rows)
    focused = None
    if len(positions) == 1:
        focused = positions[0]
    else:
        focused = next(
            (item for item in positions if item.product_id in {"", deployment.product_id}),
            None,
        )
    return DeploymentSnapshot(
        deployment=deployment,
        position=focused,
        orders=tuple(_order_from_row(row) for row in order_rows),
        fills=tuple(_fill_from_row(row) for row in fill_rows),
        intents=tuple(_intent_from_row(row) for row in intent_rows),
        positions=positions,
        instrument_runtimes=tuple(_runtime_from_row(row) for row in runtime_rows),
    )


async def _summary_snapshot(
    connection: AsyncConnection, deployment: Deployment
) -> DeploymentSummarySnapshot:
    """Load positions, overlays, and counts without historical orders or fills."""
    position_rows = (
        (
            await connection.execute(
                select(execution_positions).where(
                    execution_positions.c.deployment_id == deployment.id
                )
            )
        )
        .mappings()
        .all()
    )
    runtime_rows = (
        (
            await connection.execute(
                select(execution_instrument_state).where(
                    execution_instrument_state.c.deployment_id == deployment.id
                )
            )
        )
        .mappings()
        .all()
    )
    fill_count = int(
        (
            await connection.execute(
                select(func.count())
                .select_from(execution_fills)
                .where(execution_fills.c.deployment_id == deployment.id)
            )
        ).scalar_one()
    )
    working_orders = int(
        (
            await connection.execute(
                select(func.count())
                .select_from(execution_orders)
                .where(execution_orders.c.deployment_id == deployment.id)
                .where(
                    execution_orders.c.status.in_(
                        (
                            OrderStatus.OPEN.value,
                            OrderStatus.PENDING.value,
                            OrderStatus.UNKNOWN.value,
                        )
                    )
                )
            )
        ).scalar_one()
    )
    open_order_rows = (
        (
            await connection.execute(
                select(execution_orders)
                .where(execution_orders.c.deployment_id == deployment.id)
                .where(
                    execution_orders.c.status.in_(
                        (
                            OrderStatus.OPEN.value,
                            OrderStatus.PENDING.value,
                            OrderStatus.UNKNOWN.value,
                        )
                    )
                )
            )
        )
        .mappings()
        .all()
    )
    positions = tuple(_position_from_row(row) for row in position_rows)
    focused = None
    if len(positions) == 1:
        focused = positions[0]
    else:
        focused = next(
            (item for item in positions if item.product_id in {"", deployment.product_id}),
            None,
        )
    return DeploymentSummarySnapshot(
        deployment=deployment,
        position=focused,
        positions=positions,
        instrument_runtimes=tuple(_runtime_from_row(row) for row in runtime_rows),
        book_totals=DeploymentBookTotals(
            open_books=len(positions),
            working_orders=working_orders,
            fill_count=fill_count,
        ),
        open_orders=tuple(_order_from_row(row) for row in open_order_rows),
    )
