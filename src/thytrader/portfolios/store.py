"""Portfolio store contracts with in-memory (tests) and disabled (no database) stores.

:class:`PortfolioStore` persists portfolios, sleeves, and the append-only journal;
:class:`PortfolioBacktestStore` queues portfolio backtest jobs and keeps their canonical
results. PostgreSQL implements both in one transactional class
(:mod:`thytrader.persistence.postgres_portfolios`). Every mutation re-reads the aggregate,
checks the caller's revision, plans the change with :mod:`thytrader.portfolios.rules`, and
writes the plan and its journal entries together.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from thytrader.execution.ids import uuid7
from thytrader.portfolios.backtest import (
    PortfolioBacktestJob,
    PortfolioBacktestListing,
    PortfolioBacktestPlan,
    PortfolioBacktestResult,
    backtest_journal_summary,
    job_expiry,
    portfolio_backtest_fingerprint,
    portfolio_backtest_listing,
)
from thytrader.portfolios.models import (
    MAX_CONCURRENT_PORTFOLIO_BACKTESTS,
    JournalDetail,
    JournalEntry,
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
    SleeveView,
    sleeve_strategy_from_record,
)
from thytrader.portfolios.rules import (
    MutationPlan,
    journal_entry,
    plan_add_sleeve,
    plan_create,
    plan_remove_sleeve,
    plan_set_weights,
    plan_update,
    plan_update_sleeve,
    require_revision,
)
from thytrader.research.jobs import ResearchJobStatus
from thytrader.strategies.library import StrategyLibraryError, StrategyNotFoundError

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from thytrader.portfolios.models import (
        Portfolio,
        PortfolioCreateRequest,
        PortfolioUpdateRequest,
        SetWeightsRequest,
        Sleeve,
        SleeveAddRequest,
        SleeveUpdateRequest,
    )
    from thytrader.strategies.library import StrategyStore

_UNAVAILABLE = "Portfolio storage is unavailable."
_ACTIVE_JOB_STATUSES = (ResearchJobStatus.QUEUED, ResearchJobStatus.RUNNING)


class PortfolioBacktestNotFoundError(PortfolioError):
    """No stored portfolio backtest has the requested fingerprint for this portfolio."""


@runtime_checkable
class PortfolioStore(Protocol):
    """Persist portfolios, sleeves, and the append-only portfolio journal."""

    async def create(
        self, request: PortfolioCreateRequest, *, context: MutationContext
    ) -> PortfolioAggregate:
        """Create one portfolio at revision 1 and journal it."""
        ...

    async def list_page(self, *, limit: int, offset: int) -> PortfolioPage:
        """Return portfolios oldest first with their sleeves."""
        ...

    async def get(self, portfolio_id: UUID) -> PortfolioAggregate:
        """Return one portfolio with its sleeves."""
        ...

    async def update(
        self, portfolio_id: UUID, request: PortfolioUpdateRequest, *, context: MutationContext
    ) -> PortfolioAggregate:
        """Change settings, limits, or manager settings under the revision guard."""
        ...

    async def add_sleeve(
        self, portfolio_id: UUID, request: SleeveAddRequest, *, context: MutationContext
    ) -> PortfolioAggregate:
        """Add one strategy as a sleeve under the revision guard."""
        ...

    async def update_sleeve(
        self,
        portfolio_id: UUID,
        sleeve_id: UUID,
        request: SleeveUpdateRequest,
        *,
        context: MutationContext,
    ) -> PortfolioAggregate:
        """Change one sleeve's weight or note under the revision guard."""
        ...

    async def remove_sleeve(
        self,
        portfolio_id: UUID,
        sleeve_id: UUID,
        *,
        expected_revision: int,
        context: MutationContext,
    ) -> PortfolioAggregate:
        """Remove one sleeve under the revision guard."""
        ...

    async def set_weights(
        self, portfolio_id: UUID, request: SetWeightsRequest, *, context: MutationContext
    ) -> PortfolioAggregate:
        """Replace every sleeve weight (and optionally the reserve) under the revision guard."""
        ...

    async def delete(self, portfolio_id: UUID, *, expected_revision: int) -> PortfolioDeletion:
        """Delete one portfolio with its sleeves, journal, and backtests."""
        ...

    async def journal(self, portfolio_id: UUID, *, limit: int, offset: int) -> JournalPage:
        """Return journal entries newest first."""
        ...


@runtime_checkable
class PortfolioBacktestStore(Protocol):
    """Queue portfolio backtest jobs and keep their canonical results."""

    async def create_job(
        self, plan: PortfolioBacktestPlan, *, context: MutationContext
    ) -> PortfolioBacktestJob:
        """Queue one resolved plan."""
        ...

    async def get_job(self, portfolio_id: UUID, job_id: UUID) -> PortfolioBacktestJob | None:
        """Return one job of the portfolio, or None."""
        ...

    async def list_jobs(
        self, portfolio_id: UUID, *, limit: int
    ) -> tuple[PortfolioBacktestJob, ...]:
        """Return the portfolio's newest jobs first."""
        ...

    async def claim_next(self) -> UUID | None:
        """Mark the oldest queued job running when capacity allows and return its id."""
        ...

    async def load_plan(self, job_id: UUID) -> PortfolioBacktestPlan:
        """Return one job's resolved plan."""
        ...

    async def update_progress(self, job_id: UUID, *, current: int, total: int) -> None:
        """Record finished steps of a running job."""
        ...

    async def complete(self, job_id: UUID, result: PortfolioBacktestResult) -> PortfolioBacktestJob:
        """Store the result, journal ``backtest_run``, and mark the job completed."""
        ...

    async def fail(self, job_id: UUID, *, message: str, detail: str | None = None) -> None:
        """Mark one job failed with a caller-visible message."""
        ...

    async def expire_stale(self) -> int:
        """Expire overdue queued or running jobs."""
        ...

    async def recover_interrupted(self) -> int:
        """Requeue jobs left running by an API restart."""
        ...

    async def list_results(
        self, portfolio_id: UUID, *, limit: int, offset: int
    ) -> tuple[PortfolioBacktestListing, ...]:
        """Return stored results newest first."""
        ...

    async def load_result(
        self, portfolio_id: UUID, result_fingerprint: str
    ) -> PortfolioBacktestResult:
        """Return one stored result of the portfolio."""
        ...


@runtime_checkable
class PortfolioStorage(PortfolioStore, PortfolioBacktestStore, Protocol):
    """Both contracts in one object, which is how every implementation ships."""


class DisabledPortfolioStore:
    """Fail closed when no database is configured; background calls see an empty queue."""

    async def create(
        self, request: PortfolioCreateRequest, *, context: MutationContext
    ) -> PortfolioAggregate:
        """Refuse without durable storage."""
        del request, context
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def list_page(self, *, limit: int, offset: int) -> PortfolioPage:
        """Refuse so disabled storage never looks like zero portfolios."""
        del limit, offset
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def get(self, portfolio_id: UUID) -> PortfolioAggregate:
        """Refuse without durable storage."""
        del portfolio_id
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def update(
        self, portfolio_id: UUID, request: PortfolioUpdateRequest, *, context: MutationContext
    ) -> PortfolioAggregate:
        """Refuse without durable storage."""
        del portfolio_id, request, context
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def add_sleeve(
        self, portfolio_id: UUID, request: SleeveAddRequest, *, context: MutationContext
    ) -> PortfolioAggregate:
        """Refuse without durable storage."""
        del portfolio_id, request, context
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def update_sleeve(
        self,
        portfolio_id: UUID,
        sleeve_id: UUID,
        request: SleeveUpdateRequest,
        *,
        context: MutationContext,
    ) -> PortfolioAggregate:
        """Refuse without durable storage."""
        del portfolio_id, sleeve_id, request, context
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def remove_sleeve(
        self,
        portfolio_id: UUID,
        sleeve_id: UUID,
        *,
        expected_revision: int,
        context: MutationContext,
    ) -> PortfolioAggregate:
        """Refuse without durable storage."""
        del portfolio_id, sleeve_id, expected_revision, context
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def set_weights(
        self, portfolio_id: UUID, request: SetWeightsRequest, *, context: MutationContext
    ) -> PortfolioAggregate:
        """Refuse without durable storage."""
        del portfolio_id, request, context
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def delete(self, portfolio_id: UUID, *, expected_revision: int) -> PortfolioDeletion:
        """Refuse without durable storage."""
        del portfolio_id, expected_revision
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def journal(self, portfolio_id: UUID, *, limit: int, offset: int) -> JournalPage:
        """Refuse without durable storage."""
        del portfolio_id, limit, offset
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def create_job(
        self, plan: PortfolioBacktestPlan, *, context: MutationContext
    ) -> PortfolioBacktestJob:
        """Refuse without durable storage."""
        del plan, context
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def get_job(self, portfolio_id: UUID, job_id: UUID) -> PortfolioBacktestJob | None:
        """Refuse without durable storage."""
        del portfolio_id, job_id
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def list_jobs(
        self, portfolio_id: UUID, *, limit: int
    ) -> tuple[PortfolioBacktestJob, ...]:
        """Refuse without durable storage."""
        del portfolio_id, limit
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def claim_next(self) -> UUID | None:
        """No durable queue: nothing to run."""
        return None

    async def load_plan(self, job_id: UUID) -> PortfolioBacktestPlan:
        """Refuse without durable storage."""
        del job_id
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def update_progress(self, job_id: UUID, *, current: int, total: int) -> None:
        """Refuse without durable storage."""
        del job_id, current, total
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def complete(self, job_id: UUID, result: PortfolioBacktestResult) -> PortfolioBacktestJob:
        """Refuse without durable storage."""
        del job_id, result
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def fail(self, job_id: UUID, *, message: str, detail: str | None = None) -> None:
        """Refuse without durable storage."""
        del job_id, message, detail
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def expire_stale(self) -> int:
        """No durable queue: nothing expires."""
        return 0

    async def recover_interrupted(self) -> int:
        """No durable queue: nothing to recover."""
        return 0

    async def list_results(
        self, portfolio_id: UUID, *, limit: int, offset: int
    ) -> tuple[PortfolioBacktestListing, ...]:
        """Refuse without durable storage."""
        del portfolio_id, limit, offset
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def load_result(
        self, portfolio_id: UUID, result_fingerprint: str
    ) -> PortfolioBacktestResult:
        """Refuse without durable storage."""
        del portfolio_id, result_fingerprint
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)


@dataclass(slots=True)
class _StoredJob:
    """One in-memory job with its plan and who submitted it."""

    record: PortfolioBacktestJob
    plan: PortfolioBacktestPlan
    context: MutationContext


@dataclass(slots=True)
class _StoredResult:
    """One in-memory canonical result."""

    result: PortfolioBacktestResult
    fingerprint: str
    published_at: datetime


@dataclass
class InMemoryPortfolioStore:
    """Process-local portfolio and backtest store for tests.

    Strategy facts come from the injected strategy store on every read. A sleeve whose
    strategy disappeared reads as an invalid placeholder (PostgreSQL removes such sleeves
    in the strategy-deletion transaction instead).
    """

    strategies: StrategyStore
    _portfolios: dict[UUID, Portfolio] = field(default_factory=dict)
    _sleeves: dict[UUID, tuple[Sleeve, ...]] = field(default_factory=dict)
    _journal: dict[UUID, list[JournalEntry]] = field(default_factory=dict)
    _jobs: dict[UUID, _StoredJob] = field(default_factory=dict)
    _results: dict[UUID, list[_StoredResult]] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def create(
        self, request: PortfolioCreateRequest, *, context: MutationContext
    ) -> PortfolioAggregate:
        """Create one portfolio at revision 1 and journal it."""
        plan = plan_create(request, portfolio_id=uuid7(context.occurred_at), context=context)
        async with self._lock:
            self._apply(plan)
        return await self.get(plan.portfolio.portfolio_id)

    async def list_page(self, *, limit: int, offset: int) -> PortfolioPage:
        """Return portfolios oldest first with their sleeves."""
        ordered = sorted(
            self._portfolios.values(), key=lambda item: (item.created_at, str(item.portfolio_id))
        )
        page = ordered[offset : offset + limit]
        return PortfolioPage(
            portfolios=tuple([await self._aggregate(item) for item in page]),
            total=len(ordered),
        )

    async def get(self, portfolio_id: UUID) -> PortfolioAggregate:
        """Return one portfolio with its sleeves."""
        portfolio = self._portfolios.get(portfolio_id)
        if portfolio is None:
            raise PortfolioNotFoundError("Portfolio was not found.")
        return await self._aggregate(portfolio)

    async def update(
        self, portfolio_id: UUID, request: PortfolioUpdateRequest, *, context: MutationContext
    ) -> PortfolioAggregate:
        """Change settings, limits, or manager settings under the revision guard."""
        return await self._mutate(
            portfolio_id, lambda current: plan_update(current, request, context=context)
        )

    async def add_sleeve(
        self, portfolio_id: UUID, request: SleeveAddRequest, *, context: MutationContext
    ) -> PortfolioAggregate:
        """Add one strategy as a sleeve under the revision guard."""
        try:
            record = await self.strategies.get(request.strategy_id)
        except StrategyNotFoundError as error:
            raise PortfolioStrategyNotFoundError("Strategy was not found.") from error
        strategy = sleeve_strategy_from_record(record)
        sleeve_id = uuid7(context.occurred_at)
        return await self._mutate(
            portfolio_id,
            lambda current: plan_add_sleeve(
                current, strategy, request, sleeve_id=sleeve_id, context=context
            ),
        )

    async def update_sleeve(
        self,
        portfolio_id: UUID,
        sleeve_id: UUID,
        request: SleeveUpdateRequest,
        *,
        context: MutationContext,
    ) -> PortfolioAggregate:
        """Change one sleeve's weight or note under the revision guard."""
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
        return await self._mutate(
            portfolio_id,
            lambda current: plan_remove_sleeve(
                current, sleeve_id, expected_revision=expected_revision, context=context
            ),
        )

    async def set_weights(
        self, portfolio_id: UUID, request: SetWeightsRequest, *, context: MutationContext
    ) -> PortfolioAggregate:
        """Replace every sleeve weight under the revision guard."""
        return await self._mutate(
            portfolio_id, lambda current: plan_set_weights(current, request, context=context)
        )

    async def delete(self, portfolio_id: UUID, *, expected_revision: int) -> PortfolioDeletion:
        """Delete one portfolio with its sleeves, journal, and backtests."""
        async with self._lock:
            portfolio = self._portfolios.get(portfolio_id)
            if portfolio is None:
                raise PortfolioNotFoundError("Portfolio was not found.")
            require_revision(portfolio, expected_revision)
            jobs = [
                job_id
                for job_id, job in self._jobs.items()
                if job.plan.portfolio_id == portfolio_id
            ]
            deletion = PortfolioDeletion(
                portfolio_id=portfolio_id,
                name=portfolio.name,
                sleeves=len(self._sleeves.get(portfolio_id, ())),
                journal_entries=len(self._journal.get(portfolio_id, [])),
                backtests=len(self._results.get(portfolio_id, [])),
                backtest_jobs=len(jobs),
            )
            del self._portfolios[portfolio_id]
            self._sleeves.pop(portfolio_id, None)
            self._journal.pop(portfolio_id, None)
            self._results.pop(portfolio_id, None)
            for job_id in jobs:
                del self._jobs[job_id]
        return deletion

    async def journal(self, portfolio_id: UUID, *, limit: int, offset: int) -> JournalPage:
        """Return journal entries newest first."""
        if portfolio_id not in self._portfolios:
            raise PortfolioNotFoundError("Portfolio was not found.")
        entries = list(reversed(self._journal.get(portfolio_id, [])))
        return JournalPage(entries=tuple(entries[offset : offset + limit]), total=len(entries))

    async def create_job(
        self, plan: PortfolioBacktestPlan, *, context: MutationContext
    ) -> PortfolioBacktestJob:
        """Queue one resolved plan."""
        if plan.portfolio_id not in self._portfolios:
            raise PortfolioNotFoundError("Portfolio was not found.")
        now = context.occurred_at
        record = PortfolioBacktestJob(
            job_id=uuid7(now),
            portfolio_id=plan.portfolio_id,
            portfolio_revision=plan.portfolio_revision,
            status=ResearchJobStatus.QUEUED,
            created_at=now,
            updated_at=now,
            expires_at=job_expiry(now),
            evaluation_start=plan.evaluation_start,
            evaluation_end=plan.evaluation_end,
            sleeve_count=len(plan.sleeves),
            progress_current=0,
            progress_total=len(plan.sleeves) + 1,
        )
        async with self._lock:
            self._jobs[record.job_id] = _StoredJob(record=record, plan=plan, context=context)
        return record

    async def get_job(self, portfolio_id: UUID, job_id: UUID) -> PortfolioBacktestJob | None:
        """Return one job of the portfolio, or None."""
        stored = self._jobs.get(job_id)
        if stored is None or stored.record.portfolio_id != portfolio_id:
            return None
        return stored.record

    async def list_jobs(
        self, portfolio_id: UUID, *, limit: int
    ) -> tuple[PortfolioBacktestJob, ...]:
        """Return the portfolio's newest jobs first."""
        rows = [
            job.record for job in self._jobs.values() if job.record.portfolio_id == portfolio_id
        ]
        rows.sort(key=lambda item: item.created_at, reverse=True)
        return tuple(rows[:limit])

    async def claim_next(self) -> UUID | None:
        """Mark the oldest queued job running when capacity allows."""
        async with self._lock:
            running = sum(
                1 for job in self._jobs.values() if job.record.status is ResearchJobStatus.RUNNING
            )
            if running >= MAX_CONCURRENT_PORTFOLIO_BACKTESTS:
                return None
            queued = sorted(
                (
                    job
                    for job in self._jobs.values()
                    if job.record.status is ResearchJobStatus.QUEUED
                ),
                key=lambda job: job.record.created_at,
            )
            if not queued:
                return None
            job = queued[0]
            job.record = _job_update(job.record, status=ResearchJobStatus.RUNNING)
            return job.record.job_id

    async def load_plan(self, job_id: UUID) -> PortfolioBacktestPlan:
        """Return one job's resolved plan."""
        return self._require_job(job_id).plan

    async def update_progress(self, job_id: UUID, *, current: int, total: int) -> None:
        """Record finished steps of a running job."""
        async with self._lock:
            job = self._require_job(job_id)
            job.record = _job_update(
                job.record,
                status=job.record.status,
                progress_current=current,
                progress_total=total,
            )

    async def complete(self, job_id: UUID, result: PortfolioBacktestResult) -> PortfolioBacktestJob:
        """Store the result, journal ``backtest_run``, and mark the job completed."""
        fingerprint = portfolio_backtest_fingerprint(result)
        async with self._lock:
            job = self._require_job(job_id)
            now = datetime.now(UTC)
            stored = self._results.setdefault(result.portfolio_id, [])
            if all(item.fingerprint != fingerprint for item in stored):
                stored.append(
                    _StoredResult(result=result, fingerprint=fingerprint, published_at=now)
                )
            if result.portfolio_id in self._portfolios:
                self._journal.setdefault(result.portfolio_id, []).append(
                    backtest_journal_entry(
                        portfolio_id=result.portfolio_id,
                        job_id=job.record.job_id,
                        actor_context=replace(job.context, occurred_at=now),
                        result=result,
                        result_fingerprint=fingerprint,
                    )
                )
            job.record = _job_update(
                job.record,
                status=ResearchJobStatus.COMPLETED,
                progress_current=job.record.progress_total,
                result_fingerprint=fingerprint,
            )
            return job.record

    async def fail(self, job_id: UUID, *, message: str, detail: str | None = None) -> None:
        """Mark one job failed with a caller-visible message."""
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.record = _job_update(
                job.record,
                status=ResearchJobStatus.FAILED,
                error_message=message[:256],
                failed_detail=None if detail is None else detail[:500],
            )

    async def expire_stale(self) -> int:
        """Expire overdue queued or running jobs."""
        now = datetime.now(UTC)
        expired = 0
        async with self._lock:
            for job in self._jobs.values():
                if job.record.status in _ACTIVE_JOB_STATUSES and job.record.expires_at <= now:
                    job.record = _job_update(
                        job.record,
                        status=ResearchJobStatus.EXPIRED,
                        error_message="Portfolio backtest expired.",
                    )
                    expired += 1
        return expired

    async def recover_interrupted(self) -> int:
        """Requeue jobs left running."""
        recovered = 0
        async with self._lock:
            for job in self._jobs.values():
                if job.record.status is ResearchJobStatus.RUNNING:
                    job.record = _job_update(job.record, status=ResearchJobStatus.QUEUED)
                    recovered += 1
        return recovered

    async def list_results(
        self, portfolio_id: UUID, *, limit: int, offset: int
    ) -> tuple[PortfolioBacktestListing, ...]:
        """Return stored results newest first."""
        rows = sorted(
            self._results.get(portfolio_id, []), key=lambda item: item.published_at, reverse=True
        )
        return tuple(
            portfolio_backtest_listing(
                item.result, result_fingerprint=item.fingerprint, published_at=item.published_at
            )
            for item in rows[offset : offset + limit]
        )

    async def load_result(
        self, portfolio_id: UUID, result_fingerprint: str
    ) -> PortfolioBacktestResult:
        """Return one stored result of the portfolio."""
        for item in self._results.get(portfolio_id, []):
            if item.fingerprint == result_fingerprint:
                return item.result
        raise PortfolioBacktestNotFoundError("Portfolio backtest was not found.")

    def _require_job(self, job_id: UUID) -> _StoredJob:
        """Return one stored job or raise."""
        job = self._jobs.get(job_id)
        if job is None:
            raise PortfolioStorageUnavailableError("Portfolio backtest job was not found.")
        return job

    async def _mutate(
        self,
        portfolio_id: UUID,
        planner: Callable[[PortfolioAggregate], MutationPlan | None],
    ) -> PortfolioAggregate:
        """Plan against the current aggregate and apply the plan atomically."""
        async with self._lock:
            portfolio = self._portfolios.get(portfolio_id)
            if portfolio is None:
                raise PortfolioNotFoundError("Portfolio was not found.")
            current = await self._aggregate(portfolio)
            plan = planner(current)
            if plan is not None:
                self._apply(plan)
        return await self.get(portfolio_id)

    def _apply(self, plan: MutationPlan) -> None:
        """Replace the portfolio row and sleeves and append journal entries."""
        portfolio_id = plan.portfolio.portfolio_id
        self._portfolios[portfolio_id] = plan.portfolio
        self._sleeves[portfolio_id] = plan.sleeves
        self._journal.setdefault(portfolio_id, []).extend(plan.journal)

    async def _aggregate(self, portfolio: Portfolio) -> PortfolioAggregate:
        """Join sleeves with their strategy's current facts."""
        views = [
            SleeveView(sleeve=sleeve, strategy=await self._strategy(sleeve.strategy_id))
            for sleeve in self._sleeves.get(portfolio.portfolio_id, ())
        ]
        return PortfolioAggregate(portfolio=portfolio, sleeves=tuple(views))

    async def _strategy(self, strategy_id: UUID) -> SleeveStrategy:
        """Read one strategy's facts, or a placeholder when it no longer exists."""
        try:
            return sleeve_strategy_from_record(await self.strategies.get(strategy_id))
        except StrategyLibraryError:
            return SleeveStrategy(
                strategy_id=strategy_id,
                name="(deleted strategy)",
                product_id=None,
                covered_product_ids=(),
                timeframe=None,
                valid=False,
                current_fingerprint=None,
            )


def _job_update(
    record: PortfolioBacktestJob,
    *,
    status: ResearchJobStatus,
    progress_current: int | None = None,
    progress_total: int | None = None,
    error_message: str | None = None,
    failed_detail: str | None = None,
    result_fingerprint: str | None = None,
) -> PortfolioBacktestJob:
    """Return a job record with new status and optional fields."""
    return record.model_copy(
        update={
            "status": status,
            "updated_at": datetime.now(UTC),
            "progress_current": record.progress_current
            if progress_current is None
            else progress_current,
            "progress_total": record.progress_total if progress_total is None else progress_total,
            "error_message": error_message if error_message is not None else record.error_message,
            "failed_detail": failed_detail if failed_detail is not None else record.failed_detail,
            "result_fingerprint": result_fingerprint
            if result_fingerprint is not None
            else record.result_fingerprint,
        }
    )


def system_context(now: datetime | None = None) -> MutationContext:
    """Context for automatic consequences (strategy deletion, job completion)."""
    return MutationContext(actor="system", channel="system", occurred_at=now or datetime.now(UTC))


def backtest_journal_entry(
    *,
    portfolio_id: UUID,
    job_id: UUID,
    actor_context: MutationContext,
    result: PortfolioBacktestResult,
    result_fingerprint: str,
) -> JournalEntry:
    """Journal one completed backtest (shared by the PostgreSQL store)."""
    return journal_entry(
        portfolio_id,
        kind="backtest_run",
        context=actor_context,
        summary=backtest_journal_summary(result),
        revision=result.portfolio_revision,
        detail=JournalDetail(job_id=job_id, result_fingerprint=result_fingerprint),
    )
