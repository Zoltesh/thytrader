"""PostgreSQL implementation of the append-only portfolio snapshot store."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sqlalchemy import Select, desc, func, insert, or_, select

from thytrader.persistence.portfolio_history import PortfolioHistoryEntry
from thytrader.persistence.schema import portfolio_snapshots

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.portfolio.models import Portfolio


class PostgresPortfolioHistoryStore:
    """Durable append-only portfolio snapshot repository backed by PostgreSQL.

    The store is intentionally not a singleton: one instance is constructed
    per application lifespan and bound to a single engine.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind the store to a managed async engine."""
        self._engine = engine

    async def record(self, portfolio: Portfolio) -> None:
        """Insert one complete snapshot preserving all exact decimal strings."""
        snapshot = _portfolio_to_snapshot(portfolio)
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(portfolio_snapshots).values(
                    as_of=portfolio.as_of,
                    provider=portfolio.connection.provider,
                    connection_status=portfolio.connection.status,
                    demo=portfolio.demo,
                    total_usd_value=portfolio.total_value.amount,
                    snapshot=json.dumps(snapshot, ensure_ascii=False),
                )
            )

    async def list_range(
        self,
        *,
        start: datetime | None,
        max_entries: int,
    ) -> tuple[PortfolioHistoryEntry, ...]:
        """Return bounded newest-first range samples with exact decimal totals."""
        stmt = _range_sample_statement(start=start, max_entries=max_entries)
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return tuple(
            PortfolioHistoryEntry(
                as_of=row.as_of,
                total_value=row.total_usd_value,
            )
            for row in rows
        )


def _range_sample_statement(
    *,
    start: datetime | None,
    max_entries: int,
) -> Select[tuple[datetime, Decimal]]:
    """Build a PostgreSQL window query that avoids returning oversized chart payloads."""
    if max_entries < 1:
        message = "max_entries must be positive."
        raise ValueError(message)

    ordering = (portfolio_snapshots.c.as_of.asc(), portfolio_snapshots.c.id.asc())
    conditions = [portfolio_snapshots.c.as_of >= start] if start is not None else []
    if max_entries == 1:
        return (
            select(portfolio_snapshots.c.as_of, portfolio_snapshots.c.total_usd_value)
            .where(*conditions)
            .order_by(desc(portfolio_snapshots.c.as_of), desc(portfolio_snapshots.c.id))
            .limit(1)
        )

    bucketed = (
        select(
            portfolio_snapshots.c.id,
            portfolio_snapshots.c.as_of,
            portfolio_snapshots.c.total_usd_value,
            func.ntile(max_entries - 1).over(order_by=ordering).label("bucket"),
            func.row_number().over(order_by=ordering).label("oldest_rank"),
        )
        .where(*conditions)
        .subquery()
    )
    ranked = select(
        bucketed.c.as_of,
        bucketed.c.total_usd_value,
        bucketed.c.oldest_rank,
        func.row_number()
        .over(
            partition_by=bucketed.c.bucket,
            order_by=(bucketed.c.as_of.desc(), bucketed.c.id.desc()),
        )
        .label("bucket_latest_rank"),
    ).subquery()
    return (
        select(ranked.c.as_of, ranked.c.total_usd_value)
        .where(or_(ranked.c.oldest_rank == 1, ranked.c.bucket_latest_rank == 1))
        .order_by(desc(ranked.c.as_of))
    )


def _portfolio_to_snapshot(portfolio: Portfolio) -> dict[str, object]:
    """Convert a complete domain snapshot into a JSON-safe dictionary.

    Every ``Decimal`` is rendered as a fixed decimal string so the JSONB
    payload preserves the exact domain representation without binary
    floating-point loss.
    """
    return {
        "as_of": portfolio.as_of.isoformat(),
        "connection": {
            "provider": portfolio.connection.provider,
            "status": portfolio.connection.status,
            "permissions": list(portfolio.connection.permissions),
        },
        "demo": portfolio.demo,
        "total_value": {
            "amount": format(portfolio.total_value.amount, "f"),
            "currency": portfolio.total_value.currency,
        },
        "assets": [
            {
                "currency": asset.currency,
                "name": asset.name,
                "available": format(asset.available, "f"),
                "hold": format(asset.hold, "f"),
                "total": format(asset.total, "f"),
                "value": (
                    {
                        "amount": format(asset.value.amount, "f"),
                        "currency": asset.value.currency,
                    }
                    if asset.value is not None
                    else None
                ),
            }
            for asset in portfolio.assets
        ],
        "unvalued_assets": list(portfolio.unvalued_assets),
    }
