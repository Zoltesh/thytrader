"""Portfolio store contracts, their shared errors and refusal messages, and callback types.

:class:`PortfolioStore` persists portfolios, sleeves, and the append-only journal;
:class:`PortfolioBacktestStore` queues portfolio backtest jobs and keeps their canonical
results; :class:`PortfolioRuntimeStore` keeps each deployed portfolio's runtime state and
the manager's proposals (ADR 0091); :class:`PortfolioStorage` is all three in one object.
Implementations: :mod:`thytrader.portfolios.store_memory`,
:mod:`thytrader.portfolios.store_disabled`, and
:mod:`thytrader.persistence.postgres_portfolios`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from thytrader.portfolios.models import (
    JournalEntry,
    JournalPage,
    MutationContext,
    PortfolioAggregate,
    PortfolioDeletion,
    PortfolioError,
    PortfolioPage,
    PortfolioRuntimeState,
    PortfolioRuntimeView,
    SleeveStrategy,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime
    from decimal import Decimal
    from uuid import UUID

    from thytrader.portfolios.backtest import (
        PortfolioBacktestJob,
        PortfolioBacktestListing,
        PortfolioBacktestPlan,
        PortfolioBacktestResult,
    )
    from thytrader.portfolios.models import (
        PortfolioCreateRequest,
        PortfolioUpdateRequest,
        SetWeightsRequest,
        SleeveAddRequest,
        SleevesAddRequest,
        SleeveUpdateRequest,
    )
    from thytrader.portfolios.proposals import (
        Proposal,
        ProposalPage,
        ProposalSettlement,
        ProposalStatus,
    )

    ProposalBuilder = Callable[
        [PortfolioAggregate, Decimal, SleeveStrategy | None], ProposalSettlement
    ]
    """Plan a new proposal from the locked aggregate, the manager's auto-applied weight
    moved in the trailing week, and (for add-sleeve) the strategy's facts."""
    ProposalSettler = Callable[
        [PortfolioAggregate, Proposal, SleeveStrategy | None], ProposalSettlement
    ]
    """Settle one pending proposal against the locked aggregate."""


DEPLOYED_MESSAGE = (
    "This portfolio has running or paused sleeves; stop the portfolio before deleting it."
)
SLEEVE_DEPLOYED_MESSAGE = (
    "This sleeve's bot is running or paused; stop the sleeve before removing it."
)


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

    async def add_sleeves(
        self, portfolio_id: UUID, request: SleevesAddRequest, *, context: MutationContext
    ) -> PortfolioAggregate:
        """Add several sleeves atomically in one revision under the revision guard."""
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
class PortfolioRuntimeStore(Protocol):
    """Deployed-portfolio runtime state, runtime journal events, and manager proposals."""

    async def runtime_state(self, portfolio_id: UUID) -> PortfolioRuntimeState:
        """Return the portfolio's runtime state (the empty state before its first run)."""
        ...

    async def runtime_views(
        self, portfolio_ids: Sequence[UUID]
    ) -> tuple[PortfolioRuntimeView, ...]:
        """Return the named portfolios that still exist, each with sleeves and runtime state."""
        ...

    async def write_runtime(
        self,
        state: PortfolioRuntimeState,
        *,
        expected_revision: int,
        journal: Sequence[JournalEntry] = (),
    ) -> PortfolioRuntimeState | None:
        """Compare-and-set the runtime row (and append journal); None when it moved on."""
        ...

    async def append_journal(self, entries: Sequence[JournalEntry]) -> None:
        """Append runtime journal events (deployment start/pause/resume/stop)."""
        ...

    async def create_proposal(
        self,
        portfolio_id: UUID,
        *,
        now: datetime,
        strategy_id: UUID | None,
        build: ProposalBuilder,
    ) -> tuple[PortfolioAggregate, Proposal]:
        """Lock the portfolio, plan the proposal, and write it (and any applied change)."""
        ...

    async def settle_proposal(
        self,
        portfolio_id: UUID,
        proposal_id: UUID,
        *,
        strategy_id: UUID | None,
        settle: ProposalSettler,
    ) -> tuple[PortfolioAggregate, Proposal]:
        """Lock the portfolio and the proposal, then write its settlement."""
        ...

    async def get_proposal(self, portfolio_id: UUID, proposal_id: UUID) -> Proposal:
        """Return one proposal of the portfolio."""
        ...

    async def list_proposals(
        self,
        portfolio_id: UUID,
        *,
        status: ProposalStatus | None,
        limit: int,
        offset: int,
    ) -> ProposalPage:
        """Return proposals newest first, optionally of one status."""
        ...

    async def expire_proposals(self, now: datetime) -> int:
        """Mark pending proposals past their expiry as expired."""
        ...


@runtime_checkable
class PortfolioStorage(PortfolioStore, PortfolioBacktestStore, PortfolioRuntimeStore, Protocol):
    """Every contract in one object, which is how every implementation ships."""
