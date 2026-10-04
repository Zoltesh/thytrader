"""Freeze campaigns and advance approved research using the existing worker queue."""

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid7

from thytrader.backtest.submission import BacktestSubmissionRejectedError, resolve_backtest_window
from thytrader.market_data.datasets import DatasetStoreError
from thytrader.research.campaigns import (
    TERMINAL_CAMPAIGN_CASES,
    CampaignCaseState,
    CampaignCaseStatus,
    CampaignManifest,
    CampaignRecord,
    CampaignStart,
    FrozenCampaignCase,
    validation_status,
)
from thytrader.research.dataset_binding import (
    DatasetResolver,
    DatasetsMissingError,
    bind_backtest_datasets,
)
from thytrader.research.jobs import ResearchJobStatus
from thytrader.strategies.library import StrategySnapshotNotFoundError

if TYPE_CHECKING:
    from thytrader.market_data.datasets import DatasetStore
    from thytrader.persistence.postgres_backtests import PostgresBacktestResultStore
    from thytrader.persistence.postgres_campaigns import CampaignTransaction, PostgresCampaignStore
    from thytrader.persistence.postgres_research_jobs import PostgresResearchJobStore
    from thytrader.persistence.postgres_strategies import PostgresStrategyStore


class CampaignService:
    """Research-only scheduling; owns no broker, runtime control, or risk mutation."""

    def __init__(
        self,
        *,
        store: PostgresCampaignStore,
        strategies: PostgresStrategyStore,
        jobs: PostgresResearchJobStore,
        results: PostgresBacktestResultStore,
        datasets: DatasetStore,
        provider: str = "coinbase",
    ) -> None:
        """Compose existing stores and the complete-data catalog."""
        self.store, self.strategies, self.jobs = store, strategies, jobs
        self.results, self.datasets, self.provider = results, datasets, provider

    async def create(self, start: CampaignStart, *, now: datetime | None = None) -> CampaignRecord:
        """Freeze all snapshots and prospective boundaries before persisting approved intent."""
        instant = now or datetime.now(UTC)
        if start.deadline <= instant:
            raise ValueError("campaign deadline must be in the future")
        cases: list[FrozenCampaignCase] = []
        for case in start.cases:
            begins, ends = case.request.evaluation_start, case.request.evaluation_end
            if begins is None or ends is None:
                raise ValueError("campaign requires explicit windows")
            if start.kind == "prospective" and begins < instant:
                raise ValueError("prospective evaluation must begin after the campaign is frozen")
            if start.kind == "historical" and ends > instant:
                raise ValueError("historical evaluation cannot include future bars")
            snapshot = (
                await self.strategies.snapshot(case.request.strategy_id)
                if case.strategy_fingerprint is None
                else await self.strategies.load(case.strategy_fingerprint)
            )
            if snapshot.definition.strategy_id != case.request.strategy_id:
                raise ValueError("frozen snapshot belongs to a different strategy")
            cases.append(
                FrozenCampaignCase(
                    key=case.key,
                    request=case.request,
                    strategy_fingerprint=snapshot.strategy_fingerprint,
                )
            )
        manifest = CampaignManifest(
            campaign_id=uuid7(),
            name=start.name,
            kind=start.kind,
            provider=self.provider,
            frozen_at=instant,
            deadline=start.deadline,
            gates=start.gates,
            cases=tuple(cases),
        )
        record = CampaignRecord(
            manifest=manifest,
            manifest_fingerprint=manifest.fingerprint(),
            updated_at=instant,
            cases=tuple(CampaignCaseState(key=case.key) for case in cases),
        )
        await self.store.create(record)
        return record

    async def refresh(
        self, campaign_id: UUID, *, now: datetime | None = None, budget: int = 20
    ) -> CampaignRecord:
        """Advance bounded cases atomically, rotating to avoid starving ready cases."""
        if not 1 <= budget <= 20:
            raise ValueError("campaign refresh budget must be between 1 and 20")
        instant = now or datetime.now(UTC)
        async with self.store.locked(campaign_id) as transaction:
            record = transaction.record
            states = list(record.cases)
            # Timestamp rotation changes no manifest or case order and survives restarts.
            offset = int(record.updated_at.timestamp()) % len(states)
            checked = 0
            for delta in range(len(states)):
                index = (offset + delta) % len(states)
                state = states[index]
                if state.status in TERMINAL_CAMPAIGN_CASES:
                    continue
                states[index] = await self._advance(
                    transaction, record.manifest.cases[index], state, instant
                )
                checked += 1
                if checked >= budget:
                    break
            updated = record.model_copy(update={"updated_at": instant, "cases": tuple(states)})
            await transaction.save(updated)
            return updated

    async def _advance(
        self,
        transaction: CampaignTransaction,
        case: FrozenCampaignCase,
        state: CampaignCaseState,
        now: datetime,
    ) -> CampaignCaseState:
        """Observe a child before checking its deadline, or queue once complete data verifies."""
        if state.job_id is not None:
            return await self._observe(transaction.record, case, state, now)
        if now >= transaction.record.manifest.deadline:
            return state.model_copy(
                update={
                    "status": CampaignCaseStatus.EXPIRED,
                    "detail": "Validation deadline elapsed before complete data was available.",
                }
            )
        ends = case.request.evaluation_end
        if ends is None:
            raise ValueError("frozen campaign evaluation end is missing")
        if ends > now:
            return state
        try:
            snapshot = await self.strategies.load(case.strategy_fingerprint)
        except StrategySnapshotNotFoundError:
            return state.model_copy(
                update={
                    "status": CampaignCaseStatus.FAILED,
                    "detail": "Frozen strategy snapshot is missing.",
                }
            )
        resolver = DatasetResolver(
            store=self.datasets, provider=transaction.record.manifest.provider
        )
        try:
            bound = await asyncio.to_thread(
                bind_backtest_datasets, case.request, snapshot.definition, resolver
            )
            request = bound.submission(case.strategy_fingerprint)
            await asyncio.to_thread(resolve_backtest_window, request, snapshot, self.datasets)
        except DatasetsMissingError, DatasetStoreError, BacktestSubmissionRejectedError:
            return state.model_copy(
                update={
                    "detail": "Waiting for verified complete data covering the window and warmup."
                }
            )
        job_id = await transaction.queue(
            case.key, request, strategy_id=case.request.strategy_id, now=now
        )
        return state.model_copy(
            update={
                "job_id": job_id,
                "status": CampaignCaseStatus.QUEUED,
                "detail": "Frozen research queued; no deployment authority.",
            }
        )

    async def _observe(
        self,
        record: CampaignRecord,
        case: FrozenCampaignCase,
        state: CampaignCaseState,
        now: datetime,
    ) -> CampaignCaseState:
        """Turn finished child evidence into frozen gates without editing candidate rules."""
        if state.job_id is None:
            raise ValueError("campaign child job identity is missing")
        job = await self.jobs.get(state.job_id)
        if job is None:
            return state.model_copy(
                update={
                    "status": CampaignCaseStatus.FAILED,
                    "detail": "Campaign child job is missing.",
                }
            )
        if job.status is ResearchJobStatus.COMPLETED and job.result_fingerprint is not None:
            if job.updated_at > record.manifest.deadline:
                return state.model_copy(
                    update={
                        "status": CampaignCaseStatus.EXPIRED,
                        "detail": "Child research completed after the frozen validation deadline.",
                    }
                )
            result = (await self.results.load_projections((job.result_fingerprint,)))[0]
            if (
                job.strategy_fingerprint != case.strategy_fingerprint
                or result.strategy_fingerprint != case.strategy_fingerprint
            ):
                return state.model_copy(
                    update={
                        "status": CampaignCaseStatus.FAILED,
                        "detail": "Child result does not match the frozen strategy snapshot.",
                    }
                )
            status = validation_status(result, record.manifest.gates)
            return state.model_copy(
                update={
                    "status": status,
                    "result": result,
                    "detail": "Completed frozen-window research; " + status.value,
                }
            )
        if job.status in {
            ResearchJobStatus.FAILED,
            ResearchJobStatus.CANCELLED,
            ResearchJobStatus.EXPIRED,
        }:
            return state.model_copy(
                update={
                    "status": CampaignCaseStatus.FAILED,
                    "detail": "Child research job " + job.status.value,
                }
            )
        if now >= record.manifest.deadline:
            return state.model_copy(
                update={
                    "status": CampaignCaseStatus.EXPIRED,
                    "detail": "Validation deadline elapsed before child completion.",
                }
            )
        status = (
            CampaignCaseStatus.RUNNING
            if job.status is ResearchJobStatus.RUNNING
            else CampaignCaseStatus.QUEUED
        )
        return state.model_copy(update={"status": status})

    async def tick(self) -> None:
        """Advance a bounded set of previously approved campaigns from a research worker."""
        for record in await self.store.list(limit=2, pending=True):
            await self.refresh(record.manifest.campaign_id, budget=5)
