"""Process-local portfolio, backtest, runtime, and proposal store for tests.

Mirrors the PostgreSQL store's semantics (:mod:`thytrader.persistence.postgres_portfolios`):
every mutation re-reads the aggregate, checks the caller's revision, plans the change with
:mod:`thytrader.portfolios.rules`, and writes the plan and its journal entries together.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.ids import uuid7
from thytrader.execution.lifecycle import occupies_running_slot
from thytrader.portfolios.backtest import (
    PortfolioBacktestJob,
    PortfolioBacktestListing,
    PortfolioBacktestPlan,
    PortfolioBacktestResult,
    job_expiry,
    portfolio_backtest_fingerprint,
    portfolio_backtest_listing,
)
from thytrader.portfolios.models import (
    JournalEntry,
    JournalPage,
    MutationContext,
    PortfolioAggregate,
    PortfolioConflictError,
    PortfolioDeletion,
    PortfolioNotFoundError,
    PortfolioPage,
    PortfolioProposalNotFoundError,
    PortfolioRuntimeState,
    PortfolioRuntimeView,
    PortfolioStorageUnavailableError,
    PortfolioStrategyNotFoundError,
    SleeveStrategy,
    SleeveView,
    sleeve_strategy_from_record,
)
from thytrader.portfolios.proposals import (
    REBALANCE_BUDGET_WINDOW,
    Proposal,
    ProposalPage,
    ProposalSettlement,
)
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
from thytrader.portfolios.store_contracts import (
    DEPLOYED_MESSAGE,
    SLEEVE_DEPLOYED_MESSAGE,
    PortfolioBacktestNotFoundError,
)
from thytrader.portfolios.store_journal import backtest_journal_entry
from thytrader.research.jobs import ResearchJobStatus
from thytrader.strategies.library import StrategyLibraryError, StrategyNotFoundError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from uuid import UUID

    from thytrader.execution.store import ExecutionStore
    from thytrader.portfolios.models import (
        Portfolio,
        PortfolioCreateRequest,
        PortfolioUpdateRequest,
        SetWeightsRequest,
        Sleeve,
        SleeveAddRequest,
        SleevesAddRequest,
        SleeveUpdateRequest,
    )
    from thytrader.portfolios.proposals import ProposalStatus
    from thytrader.portfolios.store_contracts import ProposalBuilder, ProposalSettler
    from thytrader.strategies.library import StrategyStore


_ACTIVE_JOB_STATUSES = (ResearchJobStatus.QUEUED, ResearchJobStatus.RUNNING)


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
    in the strategy-deletion transaction instead). With an ``execution`` store, deleting a
    deployed portfolio or removing a deployed sleeve is refused like PostgreSQL does.
    """

    strategies: StrategyStore
    execution: ExecutionStore | None = None
    _portfolios: dict[UUID, Portfolio] = field(default_factory=dict)
    _sleeves: dict[UUID, tuple[Sleeve, ...]] = field(default_factory=dict)
    _journal: dict[UUID, list[JournalEntry]] = field(default_factory=dict)
    _jobs: dict[UUID, _StoredJob] = field(default_factory=dict)
    _results: dict[UUID, list[_StoredResult]] = field(default_factory=dict)
    _runtime: dict[UUID, PortfolioRuntimeState] = field(default_factory=dict)
    _proposals: dict[UUID, list[Proposal]] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def create(
        self, request: PortfolioCreateRequest, *, context: MutationContext
    ) -> PortfolioAggregate:
        """Create a complete portfolio at revision 1, or persist nothing."""
        strategies: list[SleeveStrategy] = []
        for item in request.sleeves:
            try:
                record = await self.strategies.get(item.strategy_id)
            except StrategyNotFoundError as error:
                raise PortfolioStrategyNotFoundError("Strategy was not found.") from error
            strategies.append(sleeve_strategy_from_record(record))
        sleeve_ids = tuple(sorted(uuid7(context.occurred_at) for _ in request.sleeves))
        plan = plan_create(
            request,
            portfolio_id=uuid7(context.occurred_at),
            context=context,
            strategies=strategies,
            sleeve_ids=sleeve_ids,
        )
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

    async def add_sleeves(
        self, portfolio_id: UUID, request: SleevesAddRequest, *, context: MutationContext
    ) -> PortfolioAggregate:
        """Add several strategies as sleeves in one revision (all or none)."""
        strategies: list[SleeveStrategy] = []
        for item in request.sleeves:
            try:
                record = await self.strategies.get(item.strategy_id)
            except StrategyNotFoundError as error:
                raise PortfolioStrategyNotFoundError("Strategy was not found.") from error
            strategies.append(sleeve_strategy_from_record(record))
        # Ascending ids keep the batch in request order, as the PostgreSQL store does.
        sleeve_ids = tuple(sorted(uuid7(context.occurred_at) for _ in request.sleeves))
        return await self._mutate(
            portfolio_id,
            lambda current: plan_add_sleeves(
                current, strategies, request, sleeve_ids=sleeve_ids, context=context
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
        """Remove one sleeve under the revision guard (refused while its bot is deployed)."""
        current = await self.get(portfolio_id)
        await self._require_sleeve_not_deployed(current, sleeve_id)
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
        """Delete one portfolio with its sleeves, journal, and backtests (not while deployed)."""
        await self._require_not_deployed(portfolio_id)
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
            self._runtime.pop(portfolio_id, None)
            self._proposals.pop(portfolio_id, None)
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
        """Mark the oldest queued job running (in-process harness; one runner at a time)."""
        async with self._lock:
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

    async def runtime_state(self, portfolio_id: UUID) -> PortfolioRuntimeState:
        """Return the portfolio's runtime state (empty before its first run)."""
        if portfolio_id not in self._portfolios:
            raise PortfolioNotFoundError("Portfolio was not found.")
        return self._runtime.get(portfolio_id, PortfolioRuntimeState(portfolio_id=portfolio_id))

    async def runtime_views(
        self, portfolio_ids: Sequence[UUID]
    ) -> tuple[PortfolioRuntimeView, ...]:
        """Return the named portfolios that still exist with sleeves and runtime state."""
        views: list[PortfolioRuntimeView] = []
        for portfolio_id in dict.fromkeys(portfolio_ids):
            portfolio = self._portfolios.get(portfolio_id)
            if portfolio is None:
                continue
            views.append(
                PortfolioRuntimeView(
                    aggregate=await self._aggregate(portfolio),
                    runtime=await self.runtime_state(portfolio_id),
                )
            )
        return tuple(views)

    async def write_runtime(
        self,
        state: PortfolioRuntimeState,
        *,
        expected_revision: int,
        journal: Sequence[JournalEntry] = (),
    ) -> PortfolioRuntimeState | None:
        """Compare-and-set the runtime state and append journal entries."""
        async with self._lock:
            if state.portfolio_id not in self._portfolios:
                raise PortfolioNotFoundError("Portfolio was not found.")
            current = self._runtime.get(state.portfolio_id)
            current_revision = 0 if current is None else current.revision
            if current_revision != expected_revision:
                return None
            written = replace(state, revision=expected_revision + 1)
            self._runtime[state.portfolio_id] = written
            self._journal.setdefault(state.portfolio_id, []).extend(journal)
            return written

    async def append_journal(self, entries: Sequence[JournalEntry]) -> None:
        """Append runtime journal events."""
        async with self._lock:
            for entry in entries:
                if entry.portfolio_id not in self._portfolios:
                    raise PortfolioNotFoundError("Portfolio was not found.")
                self._journal.setdefault(entry.portfolio_id, []).append(entry)

    async def create_proposal(
        self,
        portfolio_id: UUID,
        *,
        now: datetime,
        strategy_id: UUID | None,
        build: ProposalBuilder,
    ) -> tuple[PortfolioAggregate, Proposal]:
        """Plan and write one proposal (and any auto-applied change) atomically."""
        strategy = await self._strategy_facts(strategy_id)
        async with self._lock:
            portfolio = self._portfolios.get(portfolio_id)
            if portfolio is None:
                raise PortfolioNotFoundError("Portfolio was not found.")
            current = await self._aggregate(portfolio)
            moved = _auto_moved_since(self._proposals.get(portfolio_id, []), now)
            settlement = build(current, moved, strategy)
            self._settle(settlement)
        return await self.get(portfolio_id), settlement.proposal

    async def settle_proposal(
        self,
        portfolio_id: UUID,
        proposal_id: UUID,
        *,
        strategy_id: UUID | None,
        settle: ProposalSettler,
    ) -> tuple[PortfolioAggregate, Proposal]:
        """Settle one proposal against the current aggregate atomically."""
        strategy = await self._strategy_facts(strategy_id)
        async with self._lock:
            portfolio = self._portfolios.get(portfolio_id)
            if portfolio is None:
                raise PortfolioNotFoundError("Portfolio was not found.")
            proposal = self._find_proposal(portfolio_id, proposal_id)
            settlement = settle(await self._aggregate(portfolio), proposal, strategy)
            self._settle(settlement)
        return await self.get(portfolio_id), settlement.proposal

    async def get_proposal(self, portfolio_id: UUID, proposal_id: UUID) -> Proposal:
        """Return one proposal of the portfolio."""
        if portfolio_id not in self._portfolios:
            raise PortfolioNotFoundError("Portfolio was not found.")
        return self._find_proposal(portfolio_id, proposal_id)

    async def list_proposals(
        self,
        portfolio_id: UUID,
        *,
        status: ProposalStatus | None,
        limit: int,
        offset: int,
    ) -> ProposalPage:
        """Return proposals newest first, optionally of one status."""
        if portfolio_id not in self._portfolios:
            raise PortfolioNotFoundError("Portfolio was not found.")
        rows = sorted(
            (
                item
                for item in self._proposals.get(portfolio_id, [])
                if status is None or item.status == status
            ),
            key=lambda item: (item.created_at, str(item.proposal_id)),
            reverse=True,
        )
        return ProposalPage(proposals=tuple(rows[offset : offset + limit]), total=len(rows))

    async def expire_proposals(self, now: datetime) -> int:
        """Mark pending proposals past their expiry as expired."""
        expired = 0
        async with self._lock:
            for portfolio_id, rows in self._proposals.items():
                for index, item in enumerate(rows):
                    if item.status == "pending" and item.expires_at <= now:
                        rows[index] = item.model_copy(
                            update={"status": "expired", "decided_at": now, "decided_by": "system"}
                        )
                        expired += 1
                self._proposals[portfolio_id] = rows
        return expired

    def _settle(self, settlement: ProposalSettlement) -> None:
        """Upsert the proposal, append its journal, then apply its plan (PostgreSQL order)."""
        proposal = settlement.proposal
        rows = self._proposals.setdefault(proposal.portfolio_id, [])
        for index, item in enumerate(rows):
            if item.proposal_id == proposal.proposal_id:
                rows[index] = proposal
                break
        else:
            rows.append(proposal)
        self._journal.setdefault(proposal.portfolio_id, []).extend(settlement.journal)
        if settlement.plan is not None:
            self._apply(settlement.plan)

    def _find_proposal(self, portfolio_id: UUID, proposal_id: UUID) -> Proposal:
        """Return one stored proposal or raise not found."""
        for item in self._proposals.get(portfolio_id, []):
            if item.proposal_id == proposal_id:
                return item
        raise PortfolioProposalNotFoundError("Proposal was not found.")

    async def _strategy_facts(self, strategy_id: UUID | None) -> SleeveStrategy | None:
        """Read the strategy an add-sleeve proposal names (None when it names none)."""
        if strategy_id is None:
            return None
        try:
            return sleeve_strategy_from_record(await self.strategies.get(strategy_id))
        except StrategyNotFoundError as error:
            raise PortfolioStrategyNotFoundError("Strategy was not found.") from error

    async def _require_not_deployed(self, portfolio_id: UUID) -> None:
        """Refuse when any of the portfolio's bots is running or paused."""
        if self.execution is None:
            return
        deployments = await self.execution.list_deployments()
        if any(
            item.portfolio_id == portfolio_id and occupies_running_slot(item)
            for item in deployments
        ):
            raise PortfolioConflictError("portfolio_deployed", DEPLOYED_MESSAGE)

    async def _require_sleeve_not_deployed(
        self, current: PortfolioAggregate, sleeve_id: UUID
    ) -> None:
        """Refuse removing a sleeve whose bot is running or paused."""
        if self.execution is None:
            return
        strategy_id = current.sleeve(sleeve_id).sleeve.strategy_id
        deployments = await self.execution.list_deployments()
        if any(
            item.portfolio_id == current.portfolio.portfolio_id
            and item.strategy_id == strategy_id
            and occupies_running_slot(item)
            for item in deployments
        ):
            raise PortfolioConflictError("portfolio_sleeve_deployed", SLEEVE_DEPLOYED_MESSAGE)

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


def _auto_moved_since(rows: Sequence[Proposal], now: datetime) -> Decimal:
    """Weight moved by auto-applied rebalances in the trailing budget window."""
    since = now - REBALANCE_BUDGET_WINDOW
    return sum(
        (
            Decimal(item.weight_moved)
            for item in rows
            if item.kind == "rebalance"
            and item.auto_applied
            and item.status == "applied"
            and item.weight_moved is not None
            and item.decided_at is not None
            and item.decided_at >= since
        ),
        Decimal(0),
    )
