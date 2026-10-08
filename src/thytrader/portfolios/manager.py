"""The manager-loop contract: submit, approve, and decline proposals (ADR 0091).

The manager agent submits a proposal with a rationale and the evidence it cites. Inside
the portfolio's manager permissions and bounds a proposal auto-applies (a paper rebalance
inside the weekly budget, pausing a running sleeve); everything else waits as ``pending``
until a person approves or declines it. Approving re-checks the change against the
portfolio as it is now; a change that no longer applies is ``failed`` with the reason.
Every step is journaled with its actor (``manager`` for the agent and its auto-applied
changes, ``operator`` for a person's decision) and the manager's rationale.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from thytrader.portfolios.deployment import deployment_mode, members, sleeve_books
from thytrader.portfolios.models import (
    JournalDetail,
    JournalEntry,
    MutationContext,
    PortfolioConflictError,
    PortfolioError,
    PortfolioValidationError,
    SetWeightsRequest,
    SleeveAddRequest,
    utc_millisecond,
)
from thytrader.portfolios.proposals import (
    AddSleeveChange,
    PauseSleeveChange,
    Proposal,
    ProposalDecisionRequest,
    ProposalSettlement,
    ProposalSubmitRequest,
    RebalanceChange,
    ResumeSleeveChange,
    approval_requirement,
    canonical_weight_text,
    expiry,
    proposal_summary,
)
from thytrader.portfolios.rules import (
    MutationPlan,
    journal_entry,
    plan_add_sleeve,
    plan_set_weights,
    require_revision,
)
from thytrader.portfolios.runtime import require_live_acknowledgement
from thytrader.trading.ids import uuid7
from thytrader.trading.models import DeploymentStatus

if TYPE_CHECKING:
    from decimal import Decimal
    from uuid import UUID

    from thytrader.portfolios.models import JournalChannel, PortfolioAggregate, SleeveStrategy
    from thytrader.portfolios.proposals import DecidedBy, ProposalPage, ProposalStatus
    from thytrader.portfolios.runtime import PortfolioRuntimeService
    from thytrader.portfolios.store import PortfolioStorage
    from thytrader.trading.store import ExecutionStore

MAX_PENDING_PROPOSALS = 20
_Change = RebalanceChange | PauseSleeveChange | ResumeSleeveChange | AddSleeveChange


@dataclass(frozen=True, slots=True)
class ProposalOutcome:
    """A proposal after a submit or decision, with the portfolio as it now stands."""

    aggregate: PortfolioAggregate
    proposal: Proposal


class ProposalService:
    """Submit, approve, decline, and read manager proposals."""

    def __init__(
        self,
        *,
        portfolios: PortfolioStorage,
        execution: ExecutionStore,
        runtime: PortfolioRuntimeService,
    ) -> None:
        """Bind the portfolio store, the deployments, and the runtime actions."""
        self._portfolios = portfolios
        self._execution = execution
        self._runtime = runtime

    async def submit(
        self,
        portfolio_id: UUID,
        request: ProposalSubmitRequest,
        *,
        channel: JournalChannel,
        now: datetime | None = None,
    ) -> ProposalOutcome:
        """Record one manager proposal; auto-apply it when the permissions allow."""
        moment = utc_millisecond(now or datetime.now(UTC))
        await self._portfolios.expire_proposals(moment)
        aggregate = await self._portfolios.get(portfolio_id)
        require_revision(aggregate.portfolio, request.revision)
        await self._require_applicable(aggregate, request.change)
        pending = await self._portfolios.list_proposals(
            portfolio_id, status="pending", limit=1, offset=0
        )
        if pending.total >= MAX_PENDING_PROPOSALS:
            raise PortfolioConflictError(
                "portfolio_proposal_limit",
                f"{MAX_PENDING_PROPOSALS} proposals already wait for a decision; ask a person "
                "to approve or decline them first.",
            )
        context = MutationContext(actor="manager", channel=channel, occurred_at=moment)
        proposal_id = uuid7(moment)
        strategy_id = (
            request.change.strategy_id if isinstance(request.change, AddSleeveChange) else None
        )
        aggregate, proposal = await self._portfolios.create_proposal(
            portfolio_id,
            now=moment,
            strategy_id=strategy_id,
            build=lambda current, moved, strategy: _submission(
                current,
                request,
                moved=moved,
                strategy=strategy,
                proposal_id=proposal_id,
                context=context,
            ),
        )
        if proposal.status == "pending" and proposal.approval_reason is None:
            return await self._auto_apply_runtime(aggregate, proposal, context=context)
        return ProposalOutcome(aggregate=aggregate, proposal=proposal)

    async def approve(
        self,
        portfolio_id: UUID,
        proposal_id: UUID,
        request: ProposalDecisionRequest,
        *,
        context: MutationContext,
    ) -> ProposalOutcome:
        """A person approves one pending proposal; its change is made now or it fails."""
        proposal, aggregate = await self._pending(portfolio_id, proposal_id)
        context = _millisecond(context)
        change = proposal.change
        if isinstance(change, ResumeSleeveChange):
            require_live_acknowledgement(
                deployment_mode(aggregate), acknowledged=request.i_understand_live
            )
        if isinstance(change, PauseSleeveChange | ResumeSleeveChange):
            return await self._approve_runtime(aggregate, proposal, request, context=context)
        strategy_id = change.strategy_id if isinstance(change, AddSleeveChange) else None
        aggregate, settled = await self._portfolios.settle_proposal(
            portfolio_id,
            proposal_id,
            strategy_id=strategy_id,
            settle=lambda current, stored, strategy: _approval(
                current, stored, strategy=strategy, note=request.note, context=context
            ),
        )
        return ProposalOutcome(aggregate=aggregate, proposal=settled)

    async def decline(
        self,
        portfolio_id: UUID,
        proposal_id: UUID,
        request: ProposalDecisionRequest,
        *,
        context: MutationContext,
    ) -> ProposalOutcome:
        """A person declines one pending proposal; nothing changes."""
        await self._pending(portfolio_id, proposal_id)
        context = _millisecond(context)
        aggregate, settled = await self._portfolios.settle_proposal(
            portfolio_id,
            proposal_id,
            strategy_id=None,
            settle=lambda current, stored, _strategy: _closing(
                current,
                stored,
                status="declined",
                context=context,
                note=request.note,
            ),
        )
        return ProposalOutcome(aggregate=aggregate, proposal=settled)

    async def get(self, portfolio_id: UUID, proposal_id: UUID) -> Proposal:
        """One proposal (expired ones read as expired)."""
        await self._portfolios.expire_proposals(utc_millisecond(datetime.now(UTC)))
        return await self._portfolios.get_proposal(portfolio_id, proposal_id)

    async def list_proposals(
        self, portfolio_id: UUID, *, status: ProposalStatus | None, limit: int, offset: int
    ) -> ProposalPage:
        """Proposals newest first, optionally of one status."""
        await self._portfolios.expire_proposals(utc_millisecond(datetime.now(UTC)))
        return await self._portfolios.list_proposals(
            portfolio_id, status=status, limit=limit, offset=offset
        )

    async def _pending(
        self, portfolio_id: UUID, proposal_id: UUID
    ) -> tuple[Proposal, PortfolioAggregate]:
        """The proposal (which must still be pending) and the current portfolio."""
        proposal = await self.get(portfolio_id, proposal_id)
        _require_pending(proposal)
        return proposal, await self._portfolios.get(portfolio_id)

    async def _require_applicable(self, aggregate: PortfolioAggregate, change: _Change) -> None:
        """Refuse a proposal that cannot apply to the portfolio as it is."""
        if isinstance(change, AddSleeveChange):
            if not aggregate.portfolio.manager.permissions.may_propose_sleeves:
                raise PortfolioValidationError(
                    "portfolio_proposal_not_permitted",
                    "may_propose_sleeves is off: the manager may not propose new sleeves.",
                )
            return
        if isinstance(change, RebalanceChange):
            return
        aggregate.sleeve(change.sleeve_id)
        status = await self._sleeve_status(aggregate, change.sleeve_id)
        if isinstance(change, PauseSleeveChange) and status is not DeploymentStatus.RUNNING:
            raise PortfolioConflictError(
                "portfolio_sleeve_not_running", "Only a running sleeve can be paused."
            )
        if isinstance(change, ResumeSleeveChange):
            if status is not DeploymentStatus.PAUSED:
                raise PortfolioConflictError(
                    "portfolio_sleeve_not_paused", "Only a paused sleeve can be resumed."
                )
            runtime = await self._portfolios.runtime_state(aggregate.portfolio.portfolio_id)
            if runtime.breaker_latched:
                raise PortfolioConflictError(
                    "portfolio_breaker_latched",
                    "A portfolio breaker is latched; only a person can reset it, and no sleeve "
                    "resumes until then.",
                )

    async def _sleeve_status(
        self, aggregate: PortfolioAggregate, sleeve_id: UUID
    ) -> DeploymentStatus | None:
        """The status of one sleeve's current book, if it has one."""
        tagged = members(await self._execution.list_deployments(), aggregate.portfolio.portfolio_id)
        for book in sleeve_books(aggregate, tagged).sleeves:
            if book.view.sleeve.sleeve_id == sleeve_id:
                return None if book.deployment is None else book.deployment.status
        return None

    async def _auto_apply_runtime(
        self, aggregate: PortfolioAggregate, proposal: Proposal, *, context: MutationContext
    ) -> ProposalOutcome:
        """Pause the sleeve an auto-applying pause proposal names, then settle it."""
        change = proposal.change
        if not isinstance(change, PauseSleeveChange):
            return ProposalOutcome(aggregate=aggregate, proposal=proposal)
        portfolio_id = aggregate.portfolio.portfolio_id
        try:
            await self._runtime.pause(
                portfolio_id,
                context=context,
                sleeve_id=change.sleeve_id,
                reason="manager_proposal",
                note=f"Proposal {proposal.proposal_id}",
            )
        except PortfolioError as error:
            return await self._fail(portfolio_id, proposal, error, context=context)
        aggregate, settled = await self._portfolios.settle_proposal(
            portfolio_id,
            proposal.proposal_id,
            strategy_id=None,
            settle=lambda current, stored, _strategy: ProposalSettlement(
                proposal=_decided(
                    _pending_or_conflict(stored),
                    status="applied",
                    decided_by="manager",
                    at=context.occurred_at,
                    auto_applied=True,
                    applied_revision=current.portfolio.revision,
                ),
                plan=None,
                journal=(),
            ),
        )
        return ProposalOutcome(aggregate=aggregate, proposal=settled)

    async def _approve_runtime(
        self,
        aggregate: PortfolioAggregate,
        proposal: Proposal,
        request: ProposalDecisionRequest,
        *,
        context: MutationContext,
    ) -> ProposalOutcome:
        """Pause or resume the sleeve, then record the approval (or the failure)."""
        change = proposal.change
        portfolio_id = aggregate.portfolio.portfolio_id
        note = f"Approved proposal {proposal.proposal_id}"
        try:
            if isinstance(change, PauseSleeveChange):
                await self._runtime.pause(
                    portfolio_id,
                    context=context,
                    sleeve_id=change.sleeve_id,
                    reason="manager_proposal",
                    note=note,
                )
            elif isinstance(change, ResumeSleeveChange):
                await self._runtime.resume(
                    portfolio_id,
                    context=context,
                    live_acknowledged=request.i_understand_live,
                    sleeve_id=change.sleeve_id,
                    reason="manager_proposal",
                    note=note,
                )
        except PortfolioConflictError as error:
            return await self._fail(portfolio_id, proposal, error, context=context)
        aggregate, settled = await self._portfolios.settle_proposal(
            portfolio_id,
            proposal.proposal_id,
            strategy_id=None,
            settle=lambda current, stored, _strategy: _closing(
                current, stored, status="applied", context=context, note=request.note
            ),
        )
        return ProposalOutcome(aggregate=aggregate, proposal=settled)

    async def _fail(
        self,
        portfolio_id: UUID,
        proposal: Proposal,
        error: PortfolioError,
        *,
        context: MutationContext,
    ) -> ProposalOutcome:
        """Record that a proposal's change could not be made."""
        code = error.code if isinstance(error, PortfolioValidationError) else "portfolio_error"
        aggregate, settled = await self._portfolios.settle_proposal(
            portfolio_id,
            proposal.proposal_id,
            strategy_id=None,
            settle=lambda current, stored, _strategy: _failure(
                current, stored, code=code, message=str(error), context=context
            ),
        )
        return ProposalOutcome(aggregate=aggregate, proposal=settled)


def _submission(
    current: PortfolioAggregate,
    request: ProposalSubmitRequest,
    *,
    moved: Decimal,
    strategy: SleeveStrategy | None,
    proposal_id: UUID,
    context: MutationContext,
) -> ProposalSettlement:
    """Plan a new proposal under the portfolio lock: pending, or applied when it may be."""
    require_revision(current.portfolio, request.revision)
    change = request.change
    plan = _plan_change(
        current, change, strategy=strategy, context=context, proposal_id=proposal_id
    )
    requirement = approval_requirement(current, change, moved_this_week=moved)
    applied = requirement.auto_apply and isinstance(change, RebalanceChange)
    proposal = Proposal(
        proposal_id=proposal_id,
        portfolio_id=current.portfolio.portfolio_id,
        kind=change.kind,
        status="applied" if applied else "pending",
        summary=proposal_summary(
            current, change, strategy_name=None if strategy is None else strategy.name
        ),
        rationale=request.rationale,
        change=change,
        evidence=request.evidence,
        base_revision=current.portfolio.revision,
        submitted_by="manager",
        channel=context.channel,
        approval_reason=requirement.reason,
        weight_moved=(
            None
            if requirement.weight_moved is None
            else canonical_weight_text(requirement.weight_moved)
        ),
        created_at=context.occurred_at,
        expires_at=expiry(context.occurred_at),
        decided_at=context.occurred_at if applied else None,
        decided_by="manager" if applied else None,
        auto_applied=applied,
        applied_revision=(plan.portfolio.revision if applied and plan is not None else None),
    )
    submitted = _proposal_entry(current, proposal, kind="proposal_submitted", context=context)
    return ProposalSettlement(
        proposal=proposal,
        plan=plan if applied else None,
        journal=(submitted,),
    )


def _plan_change(
    current: PortfolioAggregate,
    change: _Change,
    *,
    strategy: SleeveStrategy | None,
    context: MutationContext,
    proposal_id: UUID,
) -> MutationPlan | None:
    """The portfolio mutation a rebalance or add-sleeve makes (validates it, too)."""
    if isinstance(change, RebalanceChange):
        plan = plan_set_weights(
            current,
            SetWeightsRequest(
                revision=current.portfolio.revision,
                weights=change.weights,
                cash_reserve_fraction=change.cash_reserve_fraction,
            ),
            context=context,
        )
        if plan is None:
            raise PortfolioValidationError(
                "portfolio_proposal_no_change", "These weights already match the portfolio."
            )
        return _tag_plan(plan, proposal_id)
    if isinstance(change, AddSleeveChange):
        if strategy is None:
            raise PortfolioValidationError(
                "portfolio_proposal_invalid", "An add-sleeve proposal must name a strategy."
            )
        plan = plan_add_sleeve(
            current,
            strategy,
            SleeveAddRequest(
                revision=current.portfolio.revision,
                strategy_id=change.strategy_id,
                weight_fraction=change.weight_fraction,
                note=change.note,
            ),
            sleeve_id=uuid7(context.occurred_at),
            context=context,
        )
        return _tag_plan(plan, proposal_id)
    return None


def _tag_plan(plan: MutationPlan, proposal_id: UUID) -> MutationPlan:
    """Mark a plan's journal entries as made on a manager proposal."""
    return replace(
        plan,
        journal=tuple(
            entry.model_copy(
                update={
                    "detail": entry.detail.model_copy(
                        update={"reason": "manager_proposal", "proposal_id": proposal_id}
                    )
                }
            )
            for entry in plan.journal
        ),
    )


def _approval(
    current: PortfolioAggregate,
    stored: Proposal,
    *,
    strategy: SleeveStrategy | None,
    note: str | None,
    context: MutationContext,
) -> ProposalSettlement:
    """Approve a rebalance or add-sleeve against the portfolio as it is now."""
    pending = _pending_or_conflict(stored)
    try:
        plan = _plan_change(
            current,
            pending.change,
            strategy=strategy,
            context=context,
            proposal_id=pending.proposal_id,
        )
    except PortfolioValidationError as error:
        return _failure(current, pending, code=error.code, message=str(error), context=context)
    if plan is None:
        return _failure(
            current,
            pending,
            code="portfolio_proposal_invalid",
            message="This proposal makes no portfolio change.",
            context=context,
        )
    decided = _decided(
        pending,
        status="applied",
        decided_by="operator",
        at=context.occurred_at,
        note=note,
        applied_revision=plan.portfolio.revision,
    )
    approved = _proposal_entry(current, decided, kind="proposal_approved", context=context)
    return ProposalSettlement(proposal=decided, plan=plan, journal=(approved,))


def _closing(
    current: PortfolioAggregate,
    stored: Proposal,
    *,
    status: ProposalStatus,
    context: MutationContext,
    note: str | None,
) -> ProposalSettlement:
    """Approve (after its runtime change) or decline one pending proposal."""
    pending = _pending_or_conflict(stored)
    decided = _decided(
        pending,
        status=status,
        decided_by="operator",
        at=context.occurred_at,
        note=note,
        applied_revision=current.portfolio.revision if status == "applied" else None,
    )
    kind: _ProposalJournalKind = "proposal_approved" if status == "applied" else "proposal_declined"
    entry = _proposal_entry(current, decided, kind=kind, context=context)
    return ProposalSettlement(proposal=decided, plan=None, journal=(entry,))


def _failure(
    current: PortfolioAggregate,
    stored: Proposal,
    *,
    code: str,
    message: str,
    context: MutationContext,
) -> ProposalSettlement:
    """Record that an approved (or auto-applying) change could not be made."""
    pending = _pending_or_conflict(stored)
    failed = _decided(
        pending,
        status="failed",
        decided_by="operator" if context.actor == "operator" else "system",
        at=context.occurred_at,
        failure_code=code[:64],
        failure_message=message[:500],
    )
    system = MutationContext(actor="system", channel="system", occurred_at=context.occurred_at)
    entry = _proposal_entry(current, failed, kind="proposal_failed", context=system)
    return ProposalSettlement(proposal=failed, plan=None, journal=(entry,))


def _decided(
    proposal: Proposal,
    *,
    status: ProposalStatus,
    decided_by: DecidedBy,
    at: datetime,
    note: str | None = None,
    auto_applied: bool = False,
    applied_revision: int | None = None,
    failure_code: str | None = None,
    failure_message: str | None = None,
) -> Proposal:
    """A proposal with its decision recorded (re-validated)."""
    return Proposal.model_validate(
        {
            **proposal.model_dump(),
            "status": status,
            "decided_by": decided_by,
            "decided_at": at,
            "decision_note": note,
            "auto_applied": auto_applied,
            "applied_revision": applied_revision,
            "failure_code": failure_code,
            "failure_message": failure_message,
        }
    )


def _pending_or_conflict(proposal: Proposal) -> Proposal:
    """Return the proposal when it can still be decided."""
    _require_pending(proposal)
    return proposal


def _require_pending(proposal: Proposal) -> None:
    """Only pending proposals can be approved or declined."""
    if proposal.status != "pending":
        raise PortfolioConflictError(
            "portfolio_proposal_not_pending",
            f"This proposal is {proposal.status}; only a pending proposal can be decided.",
        )


_ProposalJournalKind = Literal[
    "proposal_submitted", "proposal_approved", "proposal_declined", "proposal_failed"
]
_KIND_WORDS: dict[_ProposalJournalKind, str] = {
    "proposal_submitted": "Manager proposed",
    "proposal_approved": "Approved",
    "proposal_declined": "Declined",
    "proposal_failed": "Could not apply",
}


def _proposal_entry(
    current: PortfolioAggregate,
    proposal: Proposal,
    *,
    kind: _ProposalJournalKind,
    context: MutationContext,
) -> JournalEntry:
    """One proposal journal entry quoting the manager's rationale."""
    words = _KIND_WORDS[kind]
    suffix = ""
    if kind == "proposal_submitted":
        suffix = " (auto-applied)" if proposal.auto_applied else ""
        if proposal.status == "pending" and proposal.approval_reason is None:
            suffix = " (auto-applies within the manager's permissions)"
        elif proposal.status == "pending":
            suffix = " (waiting for approval)"
    if kind == "proposal_failed" and proposal.failure_message:
        suffix = f": {proposal.failure_message}"
    return journal_entry(
        current.portfolio.portfolio_id,
        kind=kind,
        context=context,
        summary=f"{words}: {proposal.summary}{suffix}",
        revision=current.portfolio.revision,
        detail=JournalDetail(
            reason="manager_proposal",
            proposal_id=proposal.proposal_id,
            rationale=proposal.rationale,
            note=proposal.decision_note,
        ),
    )


def _millisecond(context: MutationContext) -> MutationContext:
    """Decision instants at millisecond precision."""
    return MutationContext(
        actor=context.actor,
        channel=context.channel,
        occurred_at=utc_millisecond(context.occurred_at),
    )
