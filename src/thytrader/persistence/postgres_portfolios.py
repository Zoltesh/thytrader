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
import os
import socket
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
from thytrader.persistence.postgres_portfolio_runtime import (
    auto_moved_since,
    compare_and_set_runtime,
    insert_proposal,
    locked_proposal,
    occupied_deployment_count,
    proposal_from_row,
    runtime_rows,
    update_proposal,
)
from thytrader.persistence.postgres_research_queue import (
    PostgresResearchQueue,
    ResearchQueueUnavailableError,
)
from thytrader.persistence.schema import (
    portfolio_backtest_jobs,
    portfolio_journal_entries,
    portfolio_proposals,
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
    JournalActor,
    JournalChannel,
    JournalEntry,
    JournalPage,
    MutationContext,
    PortfolioAggregate,
    PortfolioConflictError,
    PortfolioDeletion,
    PortfolioError,
    PortfolioNotFoundError,
    PortfolioPage,
    PortfolioProposalNotFoundError,
    PortfolioRuntimeState,
    PortfolioRuntimeView,
    PortfolioStorageUnavailableError,
    PortfolioStrategyNotFoundError,
    SleeveStrategy,
    utc_millisecond,
)
from thytrader.portfolios.proposals import Proposal, ProposalPage
from thytrader.portfolios.rules import (
    MutationPlan,
    plan_add_sleeve,
    plan_add_sleeves,
    plan_create,
    plan_remove_sleeve,
    plan_set_weights,
    plan_update,
    plan_update_sleeve,
    require_revision,
)
from thytrader.portfolios.store import (
    DEPLOYED_MESSAGE,
    SLEEVE_DEPLOYED_MESSAGE,
    PortfolioBacktestNotFoundError,
    backtest_journal_entry,
)
from thytrader.research.jobs import ResearchJobStatus

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

    from thytrader.portfolios.models import (
        PortfolioCreateRequest,
        PortfolioUpdateRequest,
        SetWeightsRequest,
        SleeveAddRequest,
        SleevesAddRequest,
        SleeveUpdateRequest,
    )
    from thytrader.portfolios.proposals import ProposalSettlement, ProposalStatus
    from thytrader.portfolios.store import ProposalBuilder, ProposalSettler

_UNAVAILABLE = "Portfolio storage is unavailable."
_ACTIVE = (ResearchJobStatus.QUEUED.value, ResearchJobStatus.RUNNING.value)
_HARNESS_LEASE_SECONDS = 3_600.0
_HARNESS_MAX_ATTEMPTS = 3


class PostgresPortfolioStore:
    """Persist portfolios, sleeves, the journal, and portfolio backtests in PostgreSQL."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Use one application-managed asynchronous database engine."""
        self._engine = engine

    async def create(
        self, request: PortfolioCreateRequest, *, context: MutationContext
    ) -> PortfolioAggregate:
        """Create a portfolio, sleeves, and journal atomically at revision 1.

        Lock strategy rows FOR SHARE in id order before planning or writing, matching
        batch-add lock order and preventing strategy edits or deletion during creation.
        """
        context = _millisecond_context(context)
        sleeve_ids = tuple(sorted(uuid7(context.occurred_at) for _ in request.sleeves))
        try:
            async with self._engine.begin() as connection:
                shared = {
                    identity: await _shared_strategy(connection, identity)
                    for identity in sorted((item.strategy_id for item in request.sleeves), key=str)
                }
                plan = plan_create(
                    request,
                    portfolio_id=uuid7(context.occurred_at),
                    context=context,
                    strategies=tuple(shared[item.strategy_id] for item in request.sleeves),
                    sleeve_ids=sleeve_ids,
                )
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

    async def add_sleeves(
        self, portfolio_id: UUID, request: SleevesAddRequest, *, context: MutationContext
    ) -> PortfolioAggregate:
        """Add several sleeves in one transaction and one revision (all or none).

        Strategy rows are locked FOR SHARE in id order, before the portfolio row, like a
        single add, so concurrent batches cannot deadlock on each other.
        """
        context = _millisecond_context(context)
        # Ascending ids keep the batch in request order (sleeves load by created_at, id).
        sleeve_ids = tuple(sorted(uuid7(context.occurred_at) for _ in request.sleeves))
        try:
            async with self._engine.begin() as connection:
                shared = {
                    identity: await _shared_strategy(connection, identity)
                    for identity in sorted((item.strategy_id for item in request.sleeves), key=str)
                }
                strategies = tuple(shared[item.strategy_id] for item in request.sleeves)
                current = await load_aggregate(connection, portfolio_id, lock=True)
                plan = plan_add_sleeves(
                    current, strategies, request, sleeve_ids=sleeve_ids, context=context
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
        """Remove one sleeve under the revision guard (refused while its bot is deployed)."""
        context = _millisecond_context(context)
        key = str(portfolio_id)
        try:
            async with self._engine.begin() as connection:
                current = await load_aggregate(connection, portfolio_id, lock=True)
                require_revision(current.portfolio, expected_revision)
                strategy_id = str(current.sleeve(sleeve_id).sleeve.strategy_id)
                if await occupied_deployment_count(connection, key, strategy_id=strategy_id):
                    raise PortfolioConflictError(
                        "portfolio_sleeve_deployed", SLEEVE_DEPLOYED_MESSAGE
                    )
                plan = plan_remove_sleeve(
                    current, sleeve_id, expected_revision=expected_revision, context=context
                )
                await apply_plan(connection, plan, previous=current)
                return await load_aggregate(connection, portfolio_id, lock=False)
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error

    async def set_weights(
        self, portfolio_id: UUID, request: SetWeightsRequest, *, context: MutationContext
    ) -> PortfolioAggregate:
        """Replace every sleeve weight (and optionally the reserve) under the revision guard."""
        context = _millisecond_context(context)
        return await self._mutate(
            portfolio_id, lambda current: plan_set_weights(current, request, context=context)
        )

    async def delete(self, portfolio_id: UUID, *, expected_revision: int) -> PortfolioDeletion:
        """Delete one portfolio; sleeves, journal, jobs, and results cascade with it.

        Refused while any of its bots is running or paused; stopped bots keep their
        history and lose the portfolio tag (``ON DELETE SET NULL``).
        """
        key = str(portfolio_id)
        try:
            async with self._engine.begin() as connection:
                current = await load_aggregate(connection, portfolio_id, lock=True)
                require_revision(current.portfolio, expected_revision)
                if await occupied_deployment_count(connection, key):
                    raise PortfolioConflictError("portfolio_deployed", DEPLOYED_MESSAGE)
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
        """Claim the oldest queued job for the in-process harness under a long lease.

        Research workers claim through ``PostgresResearchQueue`` (ADR 0092).
        """
        owner = f"in-process/{socket.gethostname()[:48]}/{os.getpid()}"
        try:
            claimed = await PostgresResearchQueue(self._engine).claim(
                owner, lease_seconds=_HARNESS_LEASE_SECONDS, order=("portfolio_backtest_jobs",)
            )
        except ResearchQueueUnavailableError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error
        return None if claimed is None else claimed.job_id

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
        """Requeue running jobs whose lease expired (never a live worker's job)."""
        try:
            counts = await PostgresResearchQueue(self._engine).requeue_expired(
                max_attempts=_HARNESS_MAX_ATTEMPTS, queues=("portfolio_backtest_jobs",)
            )
        except ResearchQueueUnavailableError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error
        return counts.total

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

    async def runtime_state(self, portfolio_id: UUID) -> PortfolioRuntimeState:
        """Return the portfolio's runtime state (empty before its first run)."""
        key = str(portfolio_id)
        try:
            async with self._engine.connect() as connection:
                await _require_portfolio(connection, key)
                states = await runtime_rows(connection, (key,))
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error
        return states.get(portfolio_id, PortfolioRuntimeState(portfolio_id=portfolio_id))

    async def runtime_views(
        self, portfolio_ids: Sequence[UUID]
    ) -> tuple[PortfolioRuntimeView, ...]:
        """Return the named portfolios that still exist with sleeves and runtime state."""
        keys = [str(item) for item in dict.fromkeys(portfolio_ids)]
        if not keys:
            return ()
        statement = (
            select(portfolios)
            .where(portfolios.c.portfolio_id.in_(keys))
            .order_by(portfolios.c.portfolio_id)
        )
        try:
            async with self._engine.connect() as connection:
                rows = (await connection.execute(statement)).mappings().all()
                aggregates = await aggregates_for(connection, rows)
                states = await runtime_rows(connection, keys)
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error
        return tuple(
            PortfolioRuntimeView(
                aggregate=aggregate,
                runtime=states.get(
                    aggregate.portfolio.portfolio_id,
                    PortfolioRuntimeState(portfolio_id=aggregate.portfolio.portfolio_id),
                ),
            )
            for aggregate in aggregates
        )

    async def write_runtime(
        self,
        state: PortfolioRuntimeState,
        *,
        expected_revision: int,
        journal: Sequence[JournalEntry] = (),
    ) -> PortfolioRuntimeState | None:
        """Compare-and-set the runtime row and append journal entries in one transaction."""
        now = utc_millisecond(datetime.now(UTC))
        try:
            async with self._engine.begin() as connection:
                await _require_portfolio(connection, str(state.portfolio_id), lock=True)
                written = await compare_and_set_runtime(
                    connection, state, expected_revision=expected_revision, now=now
                )
                if written is None:
                    return None
                await insert_journal(connection, journal)
                return written
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error

    async def append_journal(self, entries: Sequence[JournalEntry]) -> None:
        """Append runtime journal events."""
        if not entries:
            return
        try:
            async with self._engine.begin() as connection:
                for key in sorted({str(entry.portfolio_id) for entry in entries}):
                    await _require_portfolio(connection, key, lock=True)
                await insert_journal(connection, entries)
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error

    async def create_proposal(
        self,
        portfolio_id: UUID,
        *,
        now: datetime,
        strategy_id: UUID | None,
        build: ProposalBuilder,
    ) -> tuple[PortfolioAggregate, Proposal]:
        """Lock (strategy, then portfolio), plan, and write the proposal and any change."""
        key = str(portfolio_id)
        try:
            async with self._engine.begin() as connection:
                strategy = (
                    None if strategy_id is None else await _shared_strategy(connection, strategy_id)
                )
                current = await load_aggregate(connection, portfolio_id, lock=True)
                moved = await auto_moved_since(connection, key, now=now)
                settlement = build(current, moved, strategy)
                await insert_proposal(connection, settlement.proposal)
                await _write_settlement(connection, current, settlement)
                return (
                    await load_aggregate(connection, portfolio_id, lock=False),
                    settlement.proposal,
                )
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error

    async def settle_proposal(
        self,
        portfolio_id: UUID,
        proposal_id: UUID,
        *,
        strategy_id: UUID | None,
        settle: ProposalSettler,
    ) -> tuple[PortfolioAggregate, Proposal]:
        """Lock (strategy, portfolio, proposal) and write the proposal's settlement."""
        key = str(portfolio_id)
        try:
            async with self._engine.begin() as connection:
                strategy = (
                    None if strategy_id is None else await _shared_strategy(connection, strategy_id)
                )
                current = await load_aggregate(connection, portfolio_id, lock=True)
                proposal = await locked_proposal(connection, key, proposal_id)
                if proposal is None:
                    raise PortfolioProposalNotFoundError("Proposal was not found.")
                settlement = settle(current, proposal, strategy)
                await update_proposal(connection, settlement.proposal)
                await _write_settlement(connection, current, settlement)
                return (
                    await load_aggregate(connection, portfolio_id, lock=False),
                    settlement.proposal,
                )
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error

    async def get_proposal(self, portfolio_id: UUID, proposal_id: UUID) -> Proposal:
        """Return one proposal of the portfolio."""
        statement = select(portfolio_proposals).where(
            portfolio_proposals.c.proposal_id == proposal_id,
            portfolio_proposals.c.portfolio_id == str(portfolio_id),
        )
        try:
            async with self._engine.connect() as connection:
                await _require_portfolio(connection, str(portfolio_id))
                row = (await connection.execute(statement)).mappings().one_or_none()
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error
        if row is None:
            raise PortfolioProposalNotFoundError("Proposal was not found.")
        return proposal_from_row(row)

    async def list_proposals(
        self,
        portfolio_id: UUID,
        *,
        status: ProposalStatus | None,
        limit: int,
        offset: int,
    ) -> ProposalPage:
        """Return proposals newest first, optionally of one status."""
        key = str(portfolio_id)
        conditions = [portfolio_proposals.c.portfolio_id == key]
        if status is not None:
            conditions.append(portfolio_proposals.c.status == status)
        statement = (
            select(portfolio_proposals)
            .where(*conditions)
            .order_by(
                portfolio_proposals.c.created_at.desc(), portfolio_proposals.c.proposal_id.desc()
            )
            .limit(limit)
            .offset(offset)
        )
        count = select(func.count()).select_from(portfolio_proposals).where(*conditions)
        try:
            async with self._engine.connect() as connection:
                await _require_portfolio(connection, key)
                rows = (await connection.execute(statement)).mappings().all()
                total = int((await connection.execute(count)).scalar_one())
        except SQLAlchemyError as error:
            raise PortfolioStorageUnavailableError(_UNAVAILABLE) from error
        return ProposalPage(proposals=tuple(proposal_from_row(row) for row in rows), total=total)

    async def expire_proposals(self, now: datetime) -> int:
        """Mark pending proposals past their expiry as expired."""
        statement = (
            update(portfolio_proposals)
            .where(
                portfolio_proposals.c.status == "pending",
                portfolio_proposals.c.expires_at <= now,
            )
            .values(status="expired", decided_at=now, decided_by="system")
        )
        return await self._bulk(statement)

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
