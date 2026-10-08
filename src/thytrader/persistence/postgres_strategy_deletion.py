"""Strategy deletion steps for the PostgreSQL strategy store.

They run inside the caller's single deletion transaction: read and lock the strategy row,
find blocking running or paused bots, count what deletion removes, delete research rows and
the snapshots no retained deployment references, and republish the risk policy without the
strategy's allocation. Stopped execution evidence is kept (ADR 0111).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from uuid import UUID

from sqlalchemy import ColumnElement, Select, Table, delete, func, or_, select

from thytrader.persistence.postgres_portfolio_rows import count_strategy_sleeves
from thytrader.persistence.postgres_risk import load_active_policy_in, publish_policy_in
from thytrader.persistence.schema import (
    deployments,
    published_backtest_results,
    published_research_run_specs,
    published_research_studies,
    research_jobs,
    research_study_strategies,
    strategies,
    strategy_dataset_bindings,
    strategy_snapshots,
)
from thytrader.risk.store import successor_without_allocation
from thytrader.strategies.library import StrategyDeletionCounts, StrategyNotFoundError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection


_ACTIVE_STATUSES = ("running", "paused")


@dataclass(frozen=True, slots=True)
class _LockedStrategy:
    """The strategy row fields that deletion needs."""

    strategy_id: str
    name: str


async def _strategy_row(
    connection: AsyncConnection, strategy_id: UUID, *, lock: bool
) -> _LockedStrategy:
    """Read (and optionally row-lock) the strategy being deleted."""
    statement = select(strategies.c.strategy_id, strategies.c.name).where(
        strategies.c.strategy_id == str(strategy_id)
    )
    if lock:
        statement = statement.with_for_update()
    row = (await connection.execute(statement)).mappings().one_or_none()
    if row is None:
        raise StrategyNotFoundError("Strategy was not found.")
    return _LockedStrategy(
        strategy_id=cast("str", row["strategy_id"]), name=cast("str", row["name"])
    )


async def _blocking_deployments(
    connection: AsyncConnection, strategy_id: str, *, lock: bool = False
) -> tuple[UUID, ...]:
    """Return running or paused deployments of the strategy (row-locked when deleting)."""
    statement = (
        select(deployments.c.id)
        .where(
            deployments.c.strategy_id == strategy_id,
            deployments.c.status.in_(_ACTIVE_STATUSES),
        )
        .order_by(deployments.c.id)
    )
    if lock:
        statement = statement.with_for_update()
    return tuple(cast("UUID", value) for value in (await connection.execute(statement)).scalars())


async def _count(connection: AsyncConnection, statement: Select[tuple[int]]) -> int:
    """Execute one COUNT query."""
    return int((await connection.execute(statement)).scalar_one())


def _studies_condition(strategy_id: str) -> ColumnElement[bool]:
    """Select every study the strategy owns or takes part in."""
    members = select(research_study_strategies.c.study_fingerprint).where(
        research_study_strategies.c.strategy_id == strategy_id
    )
    return or_(
        published_research_studies.c.strategy_id == strategy_id,
        published_research_studies.c.study_fingerprint.in_(members),
    )


def _kept_snapshot_condition(strategy_id: str) -> ColumnElement[bool]:
    """The strategy's snapshots that no retained paper or live deployment references."""
    referenced = select(deployments.c.strategy_fingerprint).where(
        deployments.c.strategy_fingerprint.is_not(None),
    )
    return (strategy_snapshots.c.strategy_id == strategy_id) & (
        strategy_snapshots.c.strategy_fingerprint.not_in(referenced)
    )


async def _deletion_counts(connection: AsyncConnection, strategy_id: str) -> StrategyDeletionCounts:
    """Count every row a deletion removes or detaches."""

    def by_strategy(table: Table) -> Select[tuple[int]]:
        """Count one research table's rows for the strategy."""
        return select(func.count()).select_from(table).where(table.c.strategy_id == strategy_id)

    def books(mode: str) -> Select[tuple[int]]:
        """Count the strategy's deployments in one mode."""
        return (
            select(func.count())
            .select_from(deployments)
            .where(deployments.c.strategy_id == strategy_id, deployments.c.mode == mode)
        )

    return StrategyDeletionCounts(
        snapshots=await _count(
            connection,
            select(func.count())
            .select_from(strategy_snapshots)
            .where(_kept_snapshot_condition(strategy_id)),
        ),
        backtests=await _count(connection, by_strategy(published_backtest_results)),
        research_runs=await _count(connection, by_strategy(published_research_run_specs)),
        studies=await _count(
            connection,
            select(func.count())
            .select_from(published_research_studies)
            .where(_studies_condition(strategy_id)),
        ),
        research_jobs=await _count(connection, by_strategy(research_jobs)),
        dataset_bindings=await _count(connection, by_strategy(strategy_dataset_bindings)),
        # This existing payload field counts removals, not retained evidence (ADR 0111).
        paper_deployments=0,
        live_deployments_kept=await _count(connection, books("live")),
        allocations_removed=await _allocation_count(connection, UUID(strategy_id)),
        portfolio_sleeves=await count_strategy_sleeves(connection, strategy_id),
    )


async def _allocation_count(connection: AsyncConnection, strategy_id: UUID) -> int:
    """Return how many active risk-policy allocations reserve capital for the strategy."""
    active = await load_active_policy_in(connection)
    return sum(1 for item in active.definition.allocations if item.strategy_id == strategy_id)


async def _delete_research(connection: AsyncConnection, strategy_id: str) -> None:
    """Delete jobs, studies, results, run specs, and bindings in dependency order."""
    await connection.execute(
        delete(research_jobs).where(research_jobs.c.strategy_id == strategy_id)
    )
    await connection.execute(
        delete(published_research_studies).where(_studies_condition(strategy_id))
    )
    await connection.execute(
        delete(published_backtest_results).where(
            published_backtest_results.c.strategy_id == strategy_id
        )
    )
    await connection.execute(
        delete(published_research_run_specs).where(
            published_research_run_specs.c.strategy_id == strategy_id
        )
    )
    await connection.execute(
        delete(strategy_dataset_bindings).where(
            strategy_dataset_bindings.c.strategy_id == strategy_id
        )
    )


async def _delete_unreferenced_snapshots(connection: AsyncConnection, strategy_id: str) -> None:
    """Delete snapshots except those a retained paper or live deployment ran."""
    await connection.execute(
        delete(strategy_snapshots).where(_kept_snapshot_condition(strategy_id))
    )


async def _remove_allocation(connection: AsyncConnection, strategy_id: UUID) -> bool:
    """Publish the next risk-policy version without the deleted strategy's allocation."""
    active = await load_active_policy_in(connection, for_update=True)
    successor = successor_without_allocation(active, strategy_id)
    if successor is None:
        return False
    await publish_policy_in(connection, successor)
    return True


def _journal_instant() -> datetime:
    """The UTC millisecond the portfolio journal records for a deletion's sleeve removals."""
    now = datetime.now(UTC)
    return now.replace(microsecond=(now.microsecond // 1_000) * 1_000)
