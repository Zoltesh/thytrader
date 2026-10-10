"""Paper futures funding in the PostgreSQL execution store (ADR 0129 §4, Alembic 0073).

Funding is book cash movement like a fill: each funding hour is recorded once in
``futures_funding_entries`` and its amount is added to ``deployments.cash`` in the same
transaction that row-locks the deployment (ADR 0121), bumping its revision. Snapshots of a
futures deployment carry its applied funding so opening-equity evidence can reverse it.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.persistence.postgres_execution_rows import _deployment_from_row
from thytrader.persistence.postgres_execution_statements import _locked_deployment_select
from thytrader.persistence.schema import deployments, futures_funding_entries
from thytrader.trading.models import (
    ExecutionConflictError,
    ExecutionStoreError,
    FundingCashFlow,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncConnection

    from thytrader.trading.models import Deployment


async def load_funding(
    connection: AsyncConnection, deployment: Deployment
) -> tuple[FundingCashFlow, ...]:
    """Applied funding of one futures deployment, oldest first; spot books have none."""
    if not is_futures_product_id(deployment.product_id):
        return ()
    table = futures_funding_entries
    rows = (
        (
            await connection.execute(
                select(table)
                .where(table.c.deployment_id == deployment.id)
                .order_by(table.c.product_id, table.c.funding_time)
            )
        )
        .mappings()
        .all()
    )
    return tuple(_flow_from_row(row) for row in rows)


async def apply_funding(
    connection: AsyncConnection,
    deployment_id: UUID,
    flows: tuple[FundingCashFlow, ...],
    *,
    expected_revision: int | None,
) -> tuple[int, Deployment]:
    """Insert unseen funding hours and move their amounts into cash under the row lock.

    Returns the number of hours applied and the deployment as written. A replayed hour is
    skipped (unique on deployment, product and funding hour), never charged twice.
    """
    row = (
        (await connection.execute(_locked_deployment_select(deployment_id)))
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ExecutionStoreError("Deployment was not found.")
    deployment = _deployment_from_row(row)
    if expected_revision is not None and deployment.revision != expected_revision:
        raise ExecutionConflictError("Deployment revision conflict.")
    total = Decimal(0)
    applied = 0
    for flow in flows:
        if flow.deployment_id != deployment_id:
            raise ExecutionStoreError("Funding belongs to a different deployment.")
        inserted = await connection.execute(
            insert(futures_funding_entries)
            .values(_flow_values(flow))
            .on_conflict_do_nothing(index_elements=["deployment_id", "product_id", "funding_time"])
            .returning(futures_funding_entries.c.funding_time)
        )
        if inserted.first() is None:
            continue
        total += flow.amount
        applied += 1
    if not applied:
        return 0, deployment
    revision = deployment.revision + 1
    cash = deployment.cash + total
    updated_at = max(flow.applied_at for flow in flows)
    await connection.execute(
        deployments.update()
        .where(deployments.c.id == deployment_id)
        .values(cash=str(cash), revision=revision, updated_at=updated_at)
    )
    return applied, replace(deployment, cash=cash, revision=revision, updated_at=updated_at)


def _flow_values(flow: FundingCashFlow) -> dict[str, object]:
    """Map one funding hour onto its row."""
    return {
        "deployment_id": flow.deployment_id,
        "product_id": flow.product_id,
        "funding_time": flow.funding_time,
        "signed_quantity": str(flow.signed_quantity),
        "mark_price": str(flow.mark_price),
        "rate": str(flow.rate),
        "amount": str(flow.amount),
        "applied_at": flow.applied_at,
    }


def _flow_from_row(row: RowMapping) -> FundingCashFlow:
    """Map one stored funding hour; malformed decimals fail closed."""
    try:
        return FundingCashFlow(
            deployment_id=row["deployment_id"],
            product_id=str(row["product_id"]),
            funding_time=row["funding_time"],
            signed_quantity=Decimal(str(row["signed_quantity"])),
            mark_price=Decimal(str(row["mark_price"])),
            rate=Decimal(str(row["rate"])),
            amount=Decimal(str(row["amount"])),
            applied_at=row["applied_at"],
        )
    except InvalidOperation as error:
        raise ExecutionStoreError("A stored funding entry is malformed.") from error
