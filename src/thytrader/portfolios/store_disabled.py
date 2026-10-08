"""Fail-closed portfolio store used when no database is configured."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.portfolios.errors import PortfolioStorageUnavailableError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

    from thytrader.portfolios.backtest import (
        PortfolioBacktestJob,
        PortfolioBacktestListing,
        PortfolioBacktestPlan,
        PortfolioBacktestResult,
    )
    from thytrader.portfolios.models import (
        JournalEntry,
        JournalPage,
        MutationContext,
        PortfolioAggregate,
        PortfolioCreateRequest,
        PortfolioDeletion,
        PortfolioPage,
        PortfolioRuntimeState,
        PortfolioRuntimeView,
        PortfolioUpdateRequest,
        SetWeightsRequest,
        SleeveAddRequest,
        SleevesAddRequest,
        SleeveUpdateRequest,
    )
    from thytrader.portfolios.proposals import Proposal, ProposalPage, ProposalStatus
    from thytrader.portfolios.store_contracts import ProposalBuilder, ProposalSettler


_UNAVAILABLE = "Portfolio storage is unavailable."


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

    async def add_sleeves(
        self, portfolio_id: UUID, request: SleevesAddRequest, *, context: MutationContext
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

    async def runtime_state(self, portfolio_id: UUID) -> PortfolioRuntimeState:
        """Refuse without durable storage."""
        del portfolio_id
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def runtime_views(
        self, portfolio_ids: Sequence[UUID]
    ) -> tuple[PortfolioRuntimeView, ...]:
        """Refuse so the worker fails closed for any tagged sleeve."""
        del portfolio_ids
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def write_runtime(
        self,
        state: PortfolioRuntimeState,
        *,
        expected_revision: int,
        journal: Sequence[JournalEntry] = (),
    ) -> PortfolioRuntimeState | None:
        """Refuse without durable storage."""
        del state, expected_revision, journal
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def append_journal(self, entries: Sequence[JournalEntry]) -> None:
        """Refuse without durable storage."""
        del entries
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def create_proposal(
        self,
        portfolio_id: UUID,
        *,
        now: datetime,
        strategy_id: UUID | None,
        build: ProposalBuilder,
    ) -> tuple[PortfolioAggregate, Proposal]:
        """Refuse without durable storage."""
        del portfolio_id, now, strategy_id, build
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def settle_proposal(
        self,
        portfolio_id: UUID,
        proposal_id: UUID,
        *,
        strategy_id: UUID | None,
        settle: ProposalSettler,
    ) -> tuple[PortfolioAggregate, Proposal]:
        """Refuse without durable storage."""
        del portfolio_id, proposal_id, strategy_id, settle
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def get_proposal(self, portfolio_id: UUID, proposal_id: UUID) -> Proposal:
        """Refuse without durable storage."""
        del portfolio_id, proposal_id
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def list_proposals(
        self,
        portfolio_id: UUID,
        *,
        status: ProposalStatus | None,
        limit: int,
        offset: int,
    ) -> ProposalPage:
        """Refuse without durable storage."""
        del portfolio_id, status, limit, offset
        raise PortfolioStorageUnavailableError(_UNAVAILABLE)

    async def expire_proposals(self, now: datetime) -> int:
        """No durable proposals: nothing expires."""
        del now
        return 0
