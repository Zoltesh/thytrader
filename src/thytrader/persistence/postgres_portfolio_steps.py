"""In-transaction steps the PostgreSQL portfolio store composes (ADR 0088).

The caller owns the transaction and the lock order (strategy row before portfolio row):
the millisecond mutation instant, the FOR SHARE read of a sleeve's strategy, the
portfolio existence guard, per-portfolio child-row counts, and the proposal settlement
write (journal entries first, then the plan).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from sqlalchemy import Table, func, select

from thytrader.persistence.postgres_portfolio_rows import (
    apply_plan,
    covered_products,
    insert_journal,
)
from thytrader.persistence.schema import portfolios, strategies
from thytrader.portfolios.errors import PortfolioNotFoundError, PortfolioStrategyNotFoundError
from thytrader.portfolios.models import (
    MutationContext,
    PortfolioAggregate,
    SleeveStrategy,
    utc_millisecond,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncConnection

    from thytrader.portfolios.proposals import ProposalSettlement


async def _write_settlement(
    connection: AsyncConnection, current: PortfolioAggregate, settlement: ProposalSettlement
) -> None:
    """Append the proposal's journal entries, then apply its portfolio plan (if any).

    The proposal event (submitted / approved) therefore precedes the change it made.
    """
    await insert_journal(connection, settlement.journal)
    if settlement.plan is not None:
        await apply_plan(connection, settlement.plan, previous=current)


def _millisecond_context(context: MutationContext) -> MutationContext:
    """Truncate the mutation instant to what a UUIDv7 encodes and PostgreSQL round-trips."""
    return MutationContext(
        actor=context.actor,
        channel=context.channel,
        occurred_at=utc_millisecond(context.occurred_at),
    )


async def _shared_strategy(connection: AsyncConnection, strategy_id: UUID) -> SleeveStrategy:
    """Read (FOR SHARE) the facts of the strategy a sleeve will hold."""
    statement = (
        select(
            strategies.c.strategy_id,
            strategies.c.name,
            strategies.c.product_id,
            strategies.c.timeframe,
            strategies.c.is_valid,
            strategies.c.current_fingerprint,
            strategies.c.document,
        )
        .where(strategies.c.strategy_id == str(strategy_id))
        .with_for_update(read=True)
    )
    row = (await connection.execute(statement)).mappings().one_or_none()
    if row is None:
        raise PortfolioStrategyNotFoundError("Strategy was not found.")
    product_id = cast("str | None", row["product_id"])
    return SleeveStrategy(
        strategy_id=strategy_id,
        name=cast("str", row["name"]),
        product_id=product_id,
        covered_product_ids=covered_products(cast("str", row["document"]), product_id),
        timeframe=cast("str | None", row["timeframe"]),
        valid=bool(row["is_valid"]),
        current_fingerprint=cast("str | None", row["current_fingerprint"]),
    )


async def _require_portfolio(
    connection: AsyncConnection, portfolio_id: str, *, lock: bool = False
) -> None:
    """Raise :class:`PortfolioNotFoundError` unless the portfolio exists."""
    statement = select(portfolios.c.portfolio_id).where(portfolios.c.portfolio_id == portfolio_id)
    if lock:
        statement = statement.with_for_update(read=True)
    if (await connection.execute(statement)).scalar_one_or_none() is None:
        raise PortfolioNotFoundError("Portfolio was not found.")


async def _count(connection: AsyncConnection, table: Table, portfolio_id: str) -> int:
    """Count one child table's rows for a portfolio."""
    statement = select(func.count()).select_from(table).where(table.c.portfolio_id == portfolio_id)
    return int((await connection.execute(statement)).scalar_one())
