"""PostgreSQL store for portfolios, the portfolio journal, and portfolio backtests (ADR 0088).

Every mutation runs in one transaction: lock the strategy row first when the change names
a strategy, then the portfolio row; re-read the aggregate; check the caller's revision;
plan the change with :mod:`thytrader.portfolios.rules`; write the portfolio row with a
revision guard, the sleeve diff, and the journal entries. Backtest results are stored as
canonical JSON and re-verified against their fingerprint on every load.
"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, cast
from uuid import UUID

from sqlalchemy import Table, Update, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.execution.ids import uuid7
from thytrader.persistence.postgres_portfolio_rows import (
    aggregates_for,
    apply_plan,
    covered_products,
    insert_journal,
    journal_from_row,
    load_aggregate,
)
from thytrader.persistence.schema import (
    portfolio_backtest_jobs,
    portfolio_journal_entries,
    portfolios,
    published_portfolio_backtests,
    strategies,
)
from thytrader.portfolios.backtest import (
    PortfolioBacktestJob,
    PortfolioBacktestListing,
    PortfolioBacktestPlan,
    PortfolioBacktestResult,
    canonical_portfolio_backtest_bytes,
    job_expiry,
    portfolio_backtest_fingerprint,
    portfolio_backtest_listing,
)
from thytrader.portfolios.models import (
    MAX_CONCURRENT_PORTFOLIO_BACKTESTS,
    JournalActor,
    JournalChannel,
    JournalPage,
    MutationContext,
    PortfolioAggregate,
    PortfolioDeletion,
    PortfolioError,
    PortfolioNotFoundError,
    PortfolioPage,
    PortfolioStorageUnavailableError,
    PortfolioStrategyNotFoundError,
    SleeveStrategy,
    utc_millisecond,
)
from thytrader.portfolios.rules import (
    MutationPlan,
    plan_add_sleeve,
    plan_create,
    plan_remove_sleeve,
    plan_set_weights,
    plan_update,
    plan_update_sleeve,
    require_revision,
)
from thytrader.portfolios.store import PortfolioBacktestNotFoundError, backtest_journal_entry
from thytrader.research.jobs import ResearchJobStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

    from thytrader.portfolios.models import (
        PortfolioCreateRequest,
        PortfolioUpdateRequest,
        SetWeightsRequest,
        SleeveAddRequest,
        SleeveUpdateRequest,
    )

_UNAVAILABLE = "Portfolio storage is unavailable."
_ACTIVE = (ResearchJobStatus.QUEUED.value, ResearchJobStatus.RUNNING.value)


class PostgresPortfolioStore:
    """Persist portfolios, sleeves, the journal, and portfolio backtests in PostgreSQL."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Use one application-managed asynchronous database engine."""
        self._engine = engine

    async def create(
        self, request: PortfolioCreateRequest, *, context: MutationContext
    ) -> PortfolioAggregate:
        """Insert one portfolio at revision 1 with its ``created`` journal entry."""
        context = _millisecond_context(context)
        plan = plan_create(request, portfolio_id=uuid7(context.occurred_at), context=context)
        try:
            async with self._engine.begin() as connection:
                await apply_plan(connection, plan, previous=None)
                return await load_aggregate(connection, plan.portfolio.portfolio_id, lock=False)
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error

    async def list_page(self, *, limit: int, offset: int) -> PortfolioPage:
        """Return portfolios oldest first with their sleeves (two queries plus a count)."""
        statement = (
            select(portfolios)
            .order_by(portfolios.c.created_at.asc(), portfolios.c.portfolio_id.asc())
            .limit(limit)
            .offset(offset)
        )
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).mappings().all()
                total = int(
                    (
                        await connection.execute(select(func.count()).select_from(portfolios))
                    ).scalar_one()
                )
                aggregates = await aggregates_for(connection, rows)
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error
        return PortfolioPage(portfolios=aggregates, total=total)

    async def get(self, portfolio_id: UUID) -> PortfolioAggregate:
        """Return one portfolio with its sleeves."""
        try:
            async with self._engine.connect() as connection:
                return await load_aggregate(connection, portfolio_id, lock=False)
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error

    async def update(
        self, portfolio_id: UUID, request: PortfolioUpdateRequest, *, context: MutationContext
    ) -> PortfolioAggregate:
        """Change settings, limits, or manager settings under the revision guard."""
        context = _millisecond_context(context)
        return await self._mutate(
            portfolio_id, lambda current: plan_update(current, request, context=context)
        )

    async def add_sleeve(
        self, portfolio_id: UUID, request: SleeveAddRequest, *, context: MutationContext
    ) -> PortfolioAggregate:
        """Add one strategy as a sleeve (strategy row locked before the portfolio row)."""
        context = _millisecond_context(context)
        sleeve_id = uuid7(context.occurred_at)
        try:
            async with self._engine.begin() as connection:
                strategy = await _shared_strategy(connection, request.strategy_id)
                current = await load_aggregate(connection, portfolio_id, lock=True)
                plan = plan_add_sleeve(
                    current, strategy, request, sleeve_id=sleeve_id, context=context
                )
                await apply_plan(connection, plan, previous=current)
                return await load_aggregate(connection, portfolio_id, lock=False)
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error

    async def update_sleeve(
        self,
        portfolio_id: UUID,
        sleeve_id: UUID,
        request: SleeveUpdateRequest,
        *,
        context: MutationContext,
    ) -> PortfolioAggregate:
        """Change one sleeve's weight or note under the revision guard."""
        context = _millisecond_context(context)
        return await self._mutate(
            portfolio_id,
            lambda current: plan_update_sleeve(current, sleeve_id, request, context=context),
        )

    async def remove_sleeve(
        self,
        portfolio_id: UUID,
        sleeve_id: UUID,
        *,
        expected_revision: int,
        context: MutationContext,
    ) -> PortfolioAggregate:
        """Remove one sleeve under the revision guard."""
        context = _millisecond_context(context)
        return await self._mutate(
            portfolio_id,
            lambda current: plan_remove_sleeve(
                current, sleeve_id, expected_revision=expected_revision, context=context
            ),
        )

    async def set_weights(
        self, portfolio_id: UUID, request: SetWeightsRequest, *, context: MutationContext
    ) -> PortfolioAggregate:
        """Replace every sleeve weight (and optionally the reserve) under the revision guard."""
        context = _millisecond_context(context)
        return await self._mutate(
            portfolio_id, lambda current: plan_set_weights(current, request, context=context)
        )

    async def delete(self, portfolio_id: UUID, *, expected_revision: int) -> PortfolioDeletion:
        """Delete one portfolio; sleeves, journal, jobs, and results cascade with it."""
        key = str(portfolio_id)
        try:
            async with self._engine.begin() as connection:
                current = await load_aggregate(connection, portfolio_id, lock=True)
                require_revision(current.portfolio, expected_revision)
                deletion = PortfolioDeletion(
                    portfolio_id=portfolio_id,
                    name=current.portfolio.name,
                    sleeves=len(current.sleeves),
                    journal_entries=await _count(connection, portfolio_journal_entries, key),
                    backtests=await _count(connection, published_portfolio_backtests, key),
                    backtest_jobs=await _count(connection, portfolio_backtest_jobs, key),
                )
                await connection.execute(
                    portfolios.delete().where(portfolios.c.portfolio_id == key)
                )
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error
        return deletion

    async def journal(self, portfolio_id: UUID, *, limit: int, offset: int) -> JournalPage:
        """Return journal entries newest first."""
        key = str(portfolio_id)
        statement = (
            select(portfolio_journal_entries)
            .where(portfolio_journal_entries.c.portfolio_id == key)
            .order_by(portfolio_journal_entries.c.sequence.desc())
            .limit(limit)
            .offset(offset)
        )
        try:
            async with self._engine.connect() as connection:
                await _require_portfolio(connection, key)
                rows = (await connection.execute(statement)).mappings().all()
                total = await _count(connection, portfolio_journal_entries, key)
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error
        return JournalPage(entries=tuple(journal_from_row(row) for row in rows), total=total)

    async def create_job(
        self, plan: PortfolioBacktestPlan, *, context: MutationContext
    ) -> PortfolioBacktestJob:
        """Queue one resolved plan for the background runner."""
        now = utc_millisecond(context.occurred_at)
        job_id = uuid7(now)
        values = {
            "job_id": job_id,
            "portfolio_id": str(plan.portfolio_id),
            "portfolio_revision": plan.portfolio_revision,
            "status": ResearchJobStatus.QUEUED.value,
            "payload": plan.model_dump_json(),
            "actor": context.actor,
            "channel": context.channel,
            "evaluation_start": plan.evaluation_start,
            "evaluation_end": plan.evaluation_end,
            "sleeve_count": len(plan.sleeves),
            "progress_current": 0,
            "progress_total": len(plan.sleeves) + 1,
            "created_at": now,
            "updated_at": now,
            "expires_at": job_expiry(now),
        }
        try:
            async with self._engine.begin() as connection:
                await _require_portfolio(connection, str(plan.portfolio_id), lock=True)
                await connection.execute(insert(portfolio_backtest_jobs).values(**values))
                return await _job(connection, job_id)
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error

    async def get_job(self, portfolio_id: UUID, job_id: UUID) -> PortfolioBacktestJob | None:
        """Return one job of the portfolio, or None."""
        statement = select(portfolio_backtest_jobs).where(
            portfolio_backtest_jobs.c.job_id == job_id,
            portfolio_backtest_jobs.c.portfolio_id == str(portfolio_id),
        )
        try:
            async with self._engine.connect() as connection:
                row = (await connection.execute(statement)).mappings().one_or_none()
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error
        return None if row is None else _job_from_row(row)

    async def list_jobs(
        self, portfolio_id: UUID, *, limit: int
    ) -> tuple[PortfolioBacktestJob, ...]:
        """Return the portfolio's newest jobs first."""
        statement = (
            select(portfolio_backtest_jobs)
            .where(portfolio_backtest_jobs.c.portfolio_id == str(portfolio_id))
            .order_by(portfolio_backtest_jobs.c.created_at.desc(), portfolio_backtest_jobs.c.job_id)
            .limit(limit)
        )
        try:
            async with self._engine.connect() as connection:
                await _require_portfolio(connection, str(portfolio_id))
                rows = (await connection.execute(statement)).mappings().all()
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error
        return tuple(_job_from_row(row) for row in rows)

    async def claim_next(self) -> UUID | None:
        """Mark the oldest queued job running when capacity allows."""
        running = select(func.count()).where(
            portfolio_backtest_jobs.c.status == ResearchJobStatus.RUNNING.value
        )
        oldest = (
            select(portfolio_backtest_jobs.c.job_id)
            .where(portfolio_backtest_jobs.c.status == ResearchJobStatus.QUEUED.value)
            .order_by(portfolio_backtest_jobs.c.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        try:
            async with self._engine.begin() as connection:
                if int((await connection.execute(running)).scalar_one()) >= (
                    MAX_CONCURRENT_PORTFOLIO_BACKTESTS
                ):
                    return None
                job_id = (await connection.execute(oldest)).scalar_one_or_none()
                if job_id is None:
                    return None
                await connection.execute(
                    update(portfolio_backtest_jobs)
                    .where(portfolio_backtest_jobs.c.job_id == job_id)
                    .values(status=ResearchJobStatus.RUNNING.value, updated_at=datetime.now(UTC))
                )
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error
        return cast("UUID", job_id)

    async def load_plan(self, job_id: UUID) -> PortfolioBacktestPlan:
        """Return one job's resolved plan."""
        statement = select(portfolio_backtest_jobs.c.payload).where(
            portfolio_backtest_jobs.c.job_id == job_id
        )
        try:
            async with self._engine.connect() as connection:
                payload = (await connection.execute(statement)).scalar_one_or_none()
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error
        if payload is None:
            raise PortfolioStorageUnavailableError("Portfolio backtest job was not found.")
        return PortfolioBacktestPlan.model_validate_json(cast("str", payload))

    async def update_progress(self, job_id: UUID, *, current: int, total: int) -> None:
        """Record finished steps of a running job."""
        await self._update_job(job_id, progress_current=current, progress_total=total)

    async def complete(self, job_id: UUID, result: PortfolioBacktestResult) -> PortfolioBacktestJob:
        """Store the canonical result, journal ``backtest_run``, and complete the job."""
        canonical = canonical_portfolio_backtest_bytes(result)
        fingerprint = f"sha256:{sha256(canonical).hexdigest()}"
        now = utc_millisecond(datetime.now(UTC))
        listing = portfolio_backtest_listing(
            result, result_fingerprint=fingerprint, published_at=now
        )
        try:
            async with self._engine.begin() as connection:
                job = await _locked_job_row(connection, job_id)
                await connection.execute(
                    insert(published_portfolio_backtests)
                    .values(
                        result_fingerprint=fingerprint,
                        portfolio_id=str(result.portfolio_id),
                        portfolio_revision=result.portfolio_revision,
                        evaluation_start=result.evaluation_start,
                        evaluation_end=result.evaluation_end,
                        listing=listing.model_dump_json(),
                        canonical_result=canonical.decode("utf-8"),
                        published_at=now,
                    )
                    .on_conflict_do_nothing(index_elements=["result_fingerprint"])
                )
                context = MutationContext(
                    actor=cast("JournalActor", job["actor"]),
                    channel=cast("JournalChannel", job["channel"]),
                    occurred_at=now,
                )
                await insert_journal(
                    connection,
                    (
                        backtest_journal_entry(
                            portfolio_id=result.portfolio_id,
                            job_id=job_id,
                            actor_context=context,
                            result=result,
                            result_fingerprint=fingerprint,
                        ),
                    ),
                )
                await connection.execute(
                    update(portfolio_backtest_jobs)
                    .where(portfolio_backtest_jobs.c.job_id == job_id)
                    .values(
                        status=ResearchJobStatus.COMPLETED.value,
                        result_fingerprint=fingerprint,
                        progress_current=portfolio_backtest_jobs.c.progress_total,
                        updated_at=now,
                    )
                )
                return await _job(connection, job_id)
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error

    async def fail(self, job_id: UUID, *, message: str, detail: str | None = None) -> None:
        """Mark one job failed with a caller-visible message."""
        await self._update_job(
            job_id,
            status=ResearchJobStatus.FAILED.value,
            error_message=message[:256],
            failed_detail=None if detail is None else detail[:500],
        )

    async def expire_stale(self) -> int:
        """Expire overdue queued or running jobs."""
        now = datetime.now(UTC)
        statement = (
            update(portfolio_backtest_jobs)
            .where(
                portfolio_backtest_jobs.c.expires_at <= now,
                portfolio_backtest_jobs.c.status.in_(_ACTIVE),
            )
            .values(
                status=ResearchJobStatus.EXPIRED.value,
                error_message="Portfolio backtest expired.",
                updated_at=now,
            )
        )
        return await self._bulk(statement)

    async def recover_interrupted(self) -> int:
        """Requeue jobs left running by an API restart."""
        statement = (
            update(portfolio_backtest_jobs)
            .where(portfolio_backtest_jobs.c.status == ResearchJobStatus.RUNNING.value)
            .values(status=ResearchJobStatus.QUEUED.value, updated_at=datetime.now(UTC))
        )
        return await self._bulk(statement)

    async def list_results(
        self, portfolio_id: UUID, *, limit: int, offset: int
    ) -> tuple[PortfolioBacktestListing, ...]:
        """Return stored results newest first (list rows only, no curves)."""
        statement = (
            select(published_portfolio_backtests.c.listing)
            .where(published_portfolio_backtests.c.portfolio_id == str(portfolio_id))
            .order_by(
                published_portfolio_backtests.c.published_at.desc(),
                published_portfolio_backtests.c.result_fingerprint.asc(),
            )
            .limit(limit)
            .offset(offset)
        )
        try:
            async with self._engine.connect() as connection:
                await _require_portfolio(connection, str(portfolio_id))
                rows = (await connection.execute(statement)).scalars().all()
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error
        return tuple(PortfolioBacktestListing.model_validate_json(cast("str", row)) for row in rows)

    async def load_result(
        self, portfolio_id: UUID, result_fingerprint: str
    ) -> PortfolioBacktestResult:
        """Load one result and re-verify its bytes against the fingerprint."""
        statement = select(published_portfolio_backtests.c.canonical_result).where(
            published_portfolio_backtests.c.result_fingerprint == result_fingerprint,
            published_portfolio_backtests.c.portfolio_id == str(portfolio_id),
        )
        try:
            async with self._engine.connect() as connection:
                stored = (await connection.execute(statement)).scalar_one_or_none()
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error
        if stored is None:
            raise PortfolioBacktestNotFoundError("Portfolio backtest was not found.")
        result = PortfolioBacktestResult.model_validate_json(cast("str", stored))
        if portfolio_backtest_fingerprint(result) != result_fingerprint:
            raise PortfolioStorageUnavailableError(
                "Stored portfolio backtest failed fingerprint verification."
            )
        return result

    async def _mutate(
        self,
        portfolio_id: UUID,
        planner: Callable[[PortfolioAggregate], MutationPlan | None],
    ) -> PortfolioAggregate:
        """Lock, re-read, plan, and apply one mutation in a transaction."""
        try:
            async with self._engine.begin() as connection:
                current = await load_aggregate(connection, portfolio_id, lock=True)
                plan = planner(current)
                if plan is None:
                    return current
                await apply_plan(connection, plan, previous=current)
                return await load_aggregate(connection, portfolio_id, lock=False)
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error

    async def _update_job(self, job_id: UUID, **values: object) -> None:
        """Update one job row's columns."""
        statement = (
            update(portfolio_backtest_jobs)
            .where(portfolio_backtest_jobs.c.job_id == job_id)
            .values(updated_at=datetime.now(UTC), **values)
        )
        await self._bulk(statement)

    async def _bulk(self, statement: Update) -> int:
        """Execute one UPDATE and return the affected row count."""
        try:
            async with self._engine.begin() as connection:
                result = await connection.execute(statement)
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error
        return int(result.rowcount or 0)


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


async def _locked_job_row(connection: AsyncConnection, job_id: UUID) -> RowMapping:
    """Lock one job row or raise when it no longer exists (portfolio deleted)."""
    statement = (
        select(portfolio_backtest_jobs)
        .where(portfolio_backtest_jobs.c.job_id == job_id)
        .with_for_update()
    )
    row = (await connection.execute(statement)).mappings().one_or_none()
    if row is None:
        raise PortfolioError("Portfolio backtest job no longer exists.")
    return row


async def _job(connection: AsyncConnection, job_id: UUID) -> PortfolioBacktestJob:
    """Read one job row as its record."""
    statement = select(portfolio_backtest_jobs).where(portfolio_backtest_jobs.c.job_id == job_id)
    row = (await connection.execute(statement)).mappings().one()
    return _job_from_row(row)


def _job_from_row(row: RowMapping) -> PortfolioBacktestJob:
    """Map one job row into its record."""
    return PortfolioBacktestJob(
        job_id=cast("UUID", row["job_id"]),
        portfolio_id=UUID(cast("str", row["portfolio_id"])),
        portfolio_revision=int(cast("int", row["portfolio_revision"])),
        status=ResearchJobStatus(cast("str", row["status"])),
        created_at=cast("datetime", row["created_at"]),
        updated_at=cast("datetime", row["updated_at"]),
        expires_at=cast("datetime", row["expires_at"]),
        evaluation_start=cast("datetime", row["evaluation_start"]),
        evaluation_end=cast("datetime", row["evaluation_end"]),
        sleeve_count=int(cast("int", row["sleeve_count"])),
        progress_current=int(cast("int", row["progress_current"])),
        progress_total=int(cast("int", row["progress_total"])),
        error_message=cast("str | None", row["error_message"]),
        failed_detail=cast("str | None", row["failed_detail"]),
        result_fingerprint=cast("str | None", row["result_fingerprint"]),
    )
