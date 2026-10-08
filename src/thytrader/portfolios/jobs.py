"""Runner for queued portfolio backtests (ADR 0088, ADR 0092).

Like research jobs, portfolio backtests run in the ``research-worker`` service, never in
the API process: a worker claims one queued job under a lease and calls :meth:`run_job`,
which submits every planned sleeve through the same
:class:`~thytrader.backtest.submission.BacktestSubmitter` single backtests use (results are
published and deduplicated by execution fingerprint), reloads each verified child result,
combines them off the event loop, and stores the canonical result with a journal entry.
:meth:`start` / :meth:`run_forever` remain only as the in-process test harness.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING

from thytrader.backtest.submission import BacktestSubmissionError, BacktestSubmissionRejectedError
from thytrader.portfolios.combine import (
    BasketInput,
    PortfolioCombinationError,
    SleeveRun,
    basket_sources,
    combine_portfolio,
)
from thytrader.portfolios.models import PortfolioError

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.backtest.results import BacktestResultReader
    from thytrader.backtest.submission import BacktestSubmitter
    from thytrader.market_data.datasets import DatasetStore
    from thytrader.portfolios.backtest import PortfolioBacktestPlan, PortfolioBacktestResult
    from thytrader.portfolios.store import PortfolioBacktestStore

_logger = logging.getLogger(__name__)
_UNAVAILABLE = "Backtest submission is unavailable."


@dataclass(frozen=True, slots=True)
class PortfolioBacktestRunner:
    """Poll the portfolio backtest queue and execute one job at a time."""

    store: PortfolioBacktestStore
    submitter: BacktestSubmitter
    results: BacktestResultReader
    datasets: DatasetStore
    poll_seconds: float = 0.5

    async def start(self, stop_event: asyncio.Event) -> asyncio.Task[None]:
        """Requeue jobs interrupted by a restart and return the polling task."""
        try:
            recovered = await self.store.recover_interrupted()
        except PortfolioError:
            recovered = 0
            _logger.warning("portfolio_backtests_recovery_unavailable")
        if recovered:
            _logger.info("portfolio_backtests_recovered count=%s", recovered)
        return asyncio.create_task(self.run_forever(stop_event), name="portfolio-backtest-runner")

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        """Process queued jobs until the stop event is set."""
        while not stop_event.is_set():
            try:
                await self.store.expire_stale()
                job_id = await self.store.claim_next()
            except PortfolioError:
                job_id = None
            if job_id is None:
                await asyncio.sleep(self.poll_seconds)
                continue
            await self.run_job(job_id)

    async def run_job(self, job_id: UUID) -> None:
        """Run one claimed job to completion or a recorded failure."""
        try:
            plan = await self.store.load_plan(job_id)
            result = await self._execute(job_id, plan)
            await self.store.complete(job_id, result)
        except BacktestSubmissionRejectedError as rejected:
            await self._fail(job_id, str(rejected), "sleeve backtest rejected")
        except BacktestSubmissionError:
            await self._fail(job_id, _UNAVAILABLE, "sleeve backtest unavailable")
        except PortfolioCombinationError as error:
            await self._fail(job_id, str(error), "combination")
        except Exception as error:  # noqa: BLE001 - a background job must record, not crash.
            _logger.warning("portfolio_backtest_failed error_class=%s", type(error).__name__)
            await self._fail(job_id, "Portfolio backtest failed.", type(error).__name__)

    async def _execute(self, job_id: UUID, plan: PortfolioBacktestPlan) -> PortfolioBacktestResult:
        """Run every sleeve, load the basket candles, and combine."""
        total = len(plan.sleeves) + 1
        runs: list[SleeveRun] = []
        for index, sleeve in enumerate(plan.sleeves):
            await self.store.update_progress(job_id, current=index, total=total)
            submitted = await self.submitter.submit(sleeve.submission)
            result = await self.results.load(submitted.result_fingerprint)
            runs.append(
                SleeveRun(
                    planned=sleeve,
                    run_fingerprint=submitted.run_fingerprint,
                    result_fingerprint=submitted.result_fingerprint,
                    result=result,
                )
            )
        await self.store.update_progress(job_id, current=len(plan.sleeves), total=total)
        return await asyncio.to_thread(self._combine, plan, tuple(runs))

    def _combine(
        self, plan: PortfolioBacktestPlan, runs: tuple[SleeveRun, ...]
    ) -> PortfolioBacktestResult:
        """Load verified basket candles and combine (blocking; runs on a worker thread)."""
        basket = tuple(
            BasketInput(
                source=source, candles=self.datasets.load_candles(source.dataset_fingerprint)
            )
            for source in basket_sources(plan)
        )
        return combine_portfolio(plan, runs, basket)

    async def _fail(self, job_id: UUID, message: str, detail: str) -> None:
        """Record a failure; storage outages are logged, never raised from the loop."""
        try:
            await self.store.fail(job_id, message=message, detail=detail)
        except PortfolioError:
            _logger.warning("portfolio_backtest_fail_unrecorded job_id=%s", job_id)
