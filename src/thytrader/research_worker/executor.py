"""Run one claimed research job inside a worker process, fenced by its lease (ADR 0092).

The services are built exactly like the API's PostgreSQL composition, so a job run here
publishes the same content-addressed results (same ``result_fingerprint``, same separate
``diagnostics_json``) it would have published when research ran inside the API.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

from pydantic import ValidationError

from thytrader.backtest.submission import PostgresBacktestSubmitter
from thytrader.market_data.datasets import DatasetStore
from thytrader.persistence.postgres_backtests import PostgresBacktestResultStore
from thytrader.persistence.postgres_campaigns import PostgresCampaignStore
from thytrader.persistence.postgres_portfolios import PostgresPortfolioStore
from thytrader.persistence.postgres_research_jobs import PostgresResearchJobStore
from thytrader.persistence.postgres_research_queue import PostgresResearchQueue
from thytrader.persistence.postgres_research_runs import PostgresResearchRunStore
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.persistence.postgres_studies import PostgresResearchStudyCatalog
from thytrader.portfolios.jobs import PortfolioBacktestRunner
from thytrader.research.campaign_service import CampaignService
from thytrader.research.jobs import (
    ResearchJobErrorCode,
    ResearchJobLeaseLostError,
    run_backtest_job,
    run_study_job,
)
from thytrader.research.studies import ResearchStudyService

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.portfolios.backtest import (
        PortfolioBacktestJob,
        PortfolioBacktestListing,
        PortfolioBacktestPlan,
        PortfolioBacktestResult,
    )
    from thytrader.portfolios.models import MutationContext
    from thytrader.research.worker_pool import ClaimedResearchJob

_logger = logging.getLogger(__name__)
_UNREADABLE = "The queued research job payload could not be read."


@dataclass(frozen=True, slots=True)
class ResearchWorkerServices:
    """The PostgreSQL-backed research services one worker process runs jobs with."""

    jobs: PostgresResearchJobStore
    queue: PostgresResearchQueue
    submitter: PostgresBacktestSubmitter
    studies: ResearchStudyService
    portfolios: PostgresPortfolioStore
    results: PostgresBacktestResultStore
    datasets: DatasetStore
    campaigns: CampaignService | None = None


def build_research_services(engine: AsyncEngine, dataset_root: Path) -> ResearchWorkerServices:
    """Compose research services the same way the API's PostgreSQL lifespan does."""
    datasets = DatasetStore(dataset_root)
    results = PostgresBacktestResultStore(
        engine,
        research_run_store=PostgresResearchRunStore(engine),
        dataset_store=datasets,
    )
    submitter = PostgresBacktestSubmitter(engine, datasets)
    studies = ResearchStudyService(
        publications=PostgresStrategyStore(engine),
        submitter=submitter,
        results=results,
        catalog=PostgresResearchStudyCatalog(engine),
        datasets=datasets,
    )
    return ResearchWorkerServices(
        jobs=PostgresResearchJobStore(engine),
        queue=PostgresResearchQueue(engine),
        submitter=submitter,
        studies=studies,
        portfolios=PostgresPortfolioStore(engine),
        results=results,
        datasets=datasets,
        campaigns=CampaignService(
            store=PostgresCampaignStore(engine),
            strategies=PostgresStrategyStore(engine),
            jobs=PostgresResearchJobStore(engine),
            results=results,
            datasets=datasets,
        ),
    )


@dataclass(frozen=True, slots=True)
class ResearchJobExecutor:
    """Execute claimed jobs under one worker's lease owner token."""

    services: ResearchWorkerServices
    owner: str

    async def execute(self, claimed: ClaimedResearchJob) -> None:
        """Run one claimed job to a recorded outcome, or abandon it if the lease is lost."""
        try:
            if claimed.queue == "research_jobs":
                await self._research(claimed)
            else:
                await self._portfolio(claimed)
        except ResearchJobLeaseLostError:
            _logger.warning(
                "research_job_lease_lost job_id=%s kind=%s", claimed.job_id, claimed.kind
            )

    async def _research(self, claimed: ClaimedResearchJob) -> None:
        """Run one backtest or study through the fenced research job store."""
        store = self.services.jobs.leased(self.owner)
        if claimed.kind == "backtest":
            await self._backtest(store, claimed.job_id)
        else:
            await self._study(store, claimed.job_id)

    async def _backtest(self, store: PostgresResearchJobStore, job_id: UUID) -> None:
        """Load and run one queued backtest."""
        try:
            request = await store.load_backtest_request(job_id)
        except KeyError, ValidationError:
            await _fail_unreadable(store, job_id)
            return
        await run_backtest_job(store, self.services.submitter, job_id, request)

    async def _study(self, store: PostgresResearchJobStore, job_id: UUID) -> None:
        """Load and run one queued composed study."""
        try:
            request = await store.load_study_request(job_id)
        except KeyError, ValidationError:
            await _fail_unreadable(store, job_id)
            return
        await run_study_job(store, self.services.studies, job_id, request)

    async def _portfolio(self, claimed: ClaimedResearchJob) -> None:
        """Run one portfolio backtest through a lease-checked portfolio store."""
        store = LeasedPortfolioBacktestStore(
            inner=self.services.portfolios,
            queue=self.services.queue,
            owner=self.owner,
        )
        runner = PortfolioBacktestRunner(
            store=store,
            submitter=self.services.submitter,
            results=self.services.results,
            datasets=self.services.datasets,
        )
        await runner.run_job(claimed.job_id)
        await self.services.queue.clear_lease("portfolio_backtest_jobs", claimed.job_id, self.owner)


async def _fail_unreadable(store: PostgresResearchJobStore, job_id: UUID) -> None:
    """Fail a job whose stored payload no longer validates."""
    await store.mark_failed(
        job_id,
        error_message=_UNREADABLE,
        error_code=ResearchJobErrorCode.RESEARCH_UNAVAILABLE,
    )


@dataclass(frozen=True, slots=True)
class LeasedPortfolioBacktestStore:
    """A portfolio backtest store whose job writes first check this worker's lease.

    ``update_progress``, ``complete``, and ``fail`` raise
    :class:`ResearchJobLeaseLostError` when the row is no longer running under
    ``owner``. The check precedes the write (the portfolio store owns its own
    transaction), so the window is one statement wide; a result is content-addressed,
    so a race could at worst journal one duplicate ``backtest_run`` entry.
    """

    inner: PostgresPortfolioStore
    queue: PostgresResearchQueue
    owner: str

    async def create_job(
        self, plan: PortfolioBacktestPlan, *, context: MutationContext
    ) -> PortfolioBacktestJob:
        """Queue one resolved plan (delegated)."""
        return await self.inner.create_job(plan, context=context)

    async def get_job(self, portfolio_id: UUID, job_id: UUID) -> PortfolioBacktestJob | None:
        """Return one job of the portfolio (delegated)."""
        return await self.inner.get_job(portfolio_id, job_id)

    async def list_jobs(
        self, portfolio_id: UUID, *, limit: int
    ) -> tuple[PortfolioBacktestJob, ...]:
        """Return the portfolio's newest jobs (delegated)."""
        return await self.inner.list_jobs(portfolio_id, limit=limit)

    async def claim_next(self) -> UUID | None:
        """Refuse: a worker claims through the lease queue, never through the store."""
        message = "Research workers claim portfolio backtests through PostgresResearchQueue."
        raise NotImplementedError(message)

    async def load_plan(self, job_id: UUID) -> PortfolioBacktestPlan:
        """Return one job's resolved plan (delegated)."""
        return await self.inner.load_plan(job_id)

    async def update_progress(self, job_id: UUID, *, current: int, total: int) -> None:
        """Record progress while this worker still holds the lease."""
        await self._require_lease(job_id)
        await self.inner.update_progress(job_id, current=current, total=total)

    async def complete(self, job_id: UUID, result: PortfolioBacktestResult) -> PortfolioBacktestJob:
        """Store the result while this worker still holds the lease."""
        await self._require_lease(job_id)
        return await self.inner.complete(job_id, result)

    async def fail(self, job_id: UUID, *, message: str, detail: str | None = None) -> None:
        """Record a failure while this worker still holds the lease."""
        await self._require_lease(job_id)
        await self.inner.fail(job_id, message=message, detail=detail)

    async def expire_stale(self) -> int:
        """Expire overdue jobs (delegated)."""
        return await self.inner.expire_stale()

    async def recover_interrupted(self) -> int:
        """Sweep expired leases (delegated)."""
        return await self.inner.recover_interrupted()

    async def list_results(
        self, portfolio_id: UUID, *, limit: int, offset: int
    ) -> tuple[PortfolioBacktestListing, ...]:
        """Return stored results (delegated)."""
        return await self.inner.list_results(portfolio_id, limit=limit, offset=offset)

    async def load_result(
        self, portfolio_id: UUID, result_fingerprint: str
    ) -> PortfolioBacktestResult:
        """Return one stored result (delegated)."""
        return await self.inner.load_result(portfolio_id, result_fingerprint)

    async def _require_lease(self, job_id: UUID) -> None:
        """Raise when the job is no longer running under this worker's lease."""
        if not await self.queue.holds_lease("portfolio_backtest_jobs", job_id, self.owner):
            message = f"Portfolio backtest {job_id} is no longer leased to this worker."
            raise ResearchJobLeaseLostError(message)
