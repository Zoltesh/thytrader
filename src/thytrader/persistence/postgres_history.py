"""PostgreSQL implementation of the append-only portfolio snapshot store."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sqlalchemy import desc, insert, select

from thytrader.persistence.portfolio_history import PortfolioHistoryEntry
from thytrader.persistence.schema import portfolio_snapshots

if TYPE_CHECKING:
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

    async def list_recent(self, *, limit: int) -> tuple[PortfolioHistoryEntry, ...]:
        """Return newest-first valuation entries with exact decimal totals."""
        stmt = (
            select(
                portfolio_snapshots.c.as_of,
                portfolio_snapshots.c.total_usd_value,
            )
            .order_by(
                desc(portfolio_snapshots.c.as_of),
                desc(portfolio_snapshots.c.id),
            )
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return tuple(
            PortfolioHistoryEntry(
                as_of=row.as_of,
                total_value=row.total_usd_value,
            )
            for row in rows
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
