"""Manager proposals: the state machine, auto-apply bounds, and permissions (ADR 0091)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import ValidationError
import pytest

from tests.portfolios.runtime_support import World, operator, portfolio, world
from thytrader.portfolios.errors import (
    PortfolioConflictError,
    PortfolioLiveAcknowledgementError,
    PortfolioRevisionConflictError,
    PortfolioValidationError,
)
from thytrader.portfolios.models import ManagerPermissions, SleeveAddRequest
from thytrader.portfolios.proposals import (
    ORDER_AUTHORITY_REFUSAL,
    ProposalDecisionRequest,
    ProposalSubmitRequest,
)
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.library import create_strategy_from_definition
from thytrader.trading.models import DeploymentStatus

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.portfolios.models import PortfolioAggregate
    from thytrader.portfolios.vocabulary import PortfolioMode

pytestmark = pytest.mark.anyio

_EVIDENCE = [{"kind": "backtest_result", "ref": "sha256:" + "c" * 64}]


def _rebalance(
    current: PortfolioAggregate, *weights: str, revision: int | None = None
) -> ProposalSubmitRequest:
    """A rebalance naming every sleeve, in order."""
    return ProposalSubmitRequest.model_validate(
        {
            "revision": revision or current.portfolio.revision,
            "change": {
                "kind": "rebalance",
                "weights": [
                    {"sleeve_id": str(view.sleeve.sleeve_id), "weight_fraction": weight}
                    for view, weight in zip(current.sleeves, weights, strict=True)
                ],
            },
            "rationale": "Trim the laggard by its walk-forward miss.",
            "evidence": _EVIDENCE,
        }
    )


def _sleeve_change(current: PortfolioAggregate, kind: str, index: int = 0) -> ProposalSubmitRequest:
    """A pause or resume of one sleeve."""
    return ProposalSubmitRequest.model_validate(
        {
            "revision": current.portfolio.revision,
            "change": {"kind": kind, "sleeve_id": str(current.sleeves[index].sleeve.sleeve_id)},
            "rationale": "Drawdown is 1.6x its backtest.",
        }
    )


async def _started(
    state: World, *, mode: PortfolioMode = "paper", permissions: ManagerPermissions | None = None
) -> PortfolioAggregate:
    """A started portfolio (live with a published policy and the acknowledgement)."""
    if mode == "live":
        await state.publish_policy()
    current, _records = await portfolio(state, mode=mode, permissions=permissions)
    await state.runtime.start(
        current.portfolio.portfolio_id,
        revision=current.portfolio.revision,
        context=operator(),
        live_acknowledged=mode == "live",
    )
    return current


async def _weights(state: World, portfolio_id: UUID) -> list[str]:
    """Current sleeve weights in creation order."""
    current = await state.portfolios.get(portfolio_id)
    return [view.sleeve.weight_fraction for view in current.sleeves]


async def test_paper_rebalance_inside_the_weekly_budget_auto_applies() -> None:
    """may_rebalance with room in the 7-day budget applies at once, journaled as the manager."""
    state = world()
    current, _ = await portfolio(
        state, permissions=ManagerPermissions(may_rebalance=True, max_weight_change_per_week="0.1")
    )
    pid = current.portfolio.portfolio_id
    outcome = await state.proposals.submit(pid, _rebalance(current, "0.45", "0.35"), channel="api")
    proposal = outcome.proposal
    assert (proposal.status, proposal.auto_applied, proposal.decided_by) == (
        "applied",
        True,
        "manager",
    )
    assert proposal.weight_moved == "0.05"
    assert proposal.applied_revision == outcome.aggregate.portfolio.revision
    assert await _weights(state, pid) == ["0.45", "0.35"]
    entries = (await state.portfolios.journal(pid, limit=2, offset=0)).entries
    assert [entry.kind for entry in entries] == ["weights_changed", "proposal_submitted"]
    assert {entry.actor for entry in entries} == {"manager"}
    assert entries[0].detail.reason == "manager_proposal"
    assert entries[0].detail.proposal_id == proposal.proposal_id
    assert entries[1].detail.rationale == "Trim the laggard by its walk-forward miss."


async def test_moves_beyond_the_remaining_budget_wait_for_approval() -> None:
    """The second move would exceed 10% in the week, so it waits with the reason."""
    state = world()
    current, _ = await portfolio(
        state, permissions=ManagerPermissions(may_rebalance=True, max_weight_change_per_week="0.1")
    )
    pid = current.portfolio.portfolio_id
    first = await state.proposals.submit(pid, _rebalance(current, "0.44", "0.36"), channel="api")
    assert first.proposal.status == "applied"
    second = await state.proposals.submit(
        pid, _rebalance(first.aggregate, "0.38", "0.42"), channel="api"
    )
    assert second.proposal.status == "pending"
    assert second.proposal.approval_reason is not None
    assert "4%" in second.proposal.approval_reason
    assert await _weights(state, pid) == ["0.44", "0.36"]


@pytest.mark.parametrize(
    ("mode", "permissions", "reason"),
    [
        ("live", ManagerPermissions(may_rebalance=True), "live portfolio"),
        ("paper", ManagerPermissions(may_rebalance=False), "may_rebalance is off"),
    ],
)
async def test_live_or_unpermitted_rebalances_always_wait(
    mode: PortfolioMode, permissions: ManagerPermissions, reason: str
) -> None:
    """A live rebalance moves real capital; without may_rebalance nothing auto-applies."""
    state = world()
    current, _ = await portfolio(state, mode=mode, permissions=permissions)
    outcome = await state.proposals.submit(
        current.portfolio.portfolio_id, _rebalance(current, "0.45", "0.35"), channel="api"
    )
    assert outcome.proposal.status == "pending"
    assert reason in str(outcome.proposal.approval_reason)


async def test_approve_applies_once_and_decline_changes_nothing() -> None:
    """Pending → applied by a person; deciding a settled proposal is a conflict."""
    state = world()
    current, _ = await portfolio(state)
    pid = current.portfolio.portfolio_id
    pending = await state.proposals.submit(pid, _rebalance(current, "0.4", "0.4"), channel="api")
    approved = await state.proposals.approve(
        pid,
        pending.proposal.proposal_id,
        ProposalDecisionRequest(note="Agreed."),
        context=operator(),
    )
    assert (approved.proposal.status, approved.proposal.decided_by) == ("applied", "operator")
    assert approved.proposal.decision_note == "Agreed."
    assert await _weights(state, pid) == ["0.4", "0.4"]
    kinds = [
        entry.kind for entry in (await state.portfolios.journal(pid, limit=2, offset=0)).entries
    ]
    assert kinds == ["weights_changed", "proposal_approved"]
    with pytest.raises(PortfolioConflictError) as raised:
        await state.proposals.decline(
            pid, pending.proposal.proposal_id, ProposalDecisionRequest(), context=operator()
        )
    assert raised.value.code == "portfolio_proposal_not_pending"
    other = await state.proposals.submit(
        pid, _rebalance(approved.aggregate, "0.3", "0.5"), channel="api"
    )
    declined = await state.proposals.decline(
        pid, other.proposal.proposal_id, ProposalDecisionRequest(), context=operator()
    )
    assert declined.proposal.status == "declined"
    assert await _weights(state, pid) == ["0.4", "0.4"]


async def test_an_approval_that_no_longer_applies_fails_with_the_reason() -> None:
    """A sleeve added after the proposal makes its weights incomplete: failed, unchanged."""
    state = world()
    current, _ = await portfolio(state, sleeves=(("BTC-USDC", "0.4"), ("ETH-USDC", "0.3")))
    pid = current.portfolio.portfolio_id
    pending = await state.proposals.submit(pid, _rebalance(current, "0.35", "0.35"), channel="api")
    definition = create_template_strategy(
        product_id="SOL-USDC", timeframe="1h", template="ema-trend"
    )
    extra = await create_strategy_from_definition(state.strategies, definition)
    await state.portfolios.add_sleeve(
        pid,
        SleeveAddRequest(
            revision=current.portfolio.revision,
            strategy_id=extra.strategy_id,
            weight_fraction="0.1",
        ),
        context=operator(),
    )
    failed = await state.proposals.approve(
        pid, pending.proposal.proposal_id, ProposalDecisionRequest(), context=operator()
    )
    assert failed.proposal.status == "failed"
    assert failed.proposal.failure_code == "portfolio_weights_incomplete"
    assert await _weights(state, pid) == ["0.4", "0.3", "0.1"]
    entry = (await state.portfolios.journal(pid, limit=1, offset=0)).entries[0]
    assert (entry.kind, entry.actor) == ("proposal_failed", "system")


async def test_pause_auto_applies_with_permission_and_otherwise_waits() -> None:
    """may_pause_sleeves pauses a running sleeve at once; without it a person decides."""
    state = world()
    current = await _started(state, permissions=ManagerPermissions(may_pause_sleeves=True))
    pid = current.portfolio.portfolio_id
    outcome = await state.proposals.submit(
        pid, _sleeve_change(current, "pause_sleeve"), channel="api"
    )
    assert (outcome.proposal.status, outcome.proposal.auto_applied) == ("applied", True)
    books = await state.tagged(pid)
    assert [book.status for book in books] == [DeploymentStatus.PAUSED, DeploymentStatus.RUNNING]
    entries = (await state.portfolios.journal(pid, limit=2, offset=0)).entries
    assert [(entry.kind, entry.actor) for entry in entries] == [
        ("deployment_paused", "manager"),
        ("proposal_submitted", "manager"),
    ]
    other = world()
    held = await _started(other)
    waiting = await other.proposals.submit(
        held.portfolio.portfolio_id, _sleeve_change(held, "pause_sleeve"), channel="api"
    )
    assert waiting.proposal.status == "pending"
    approved = await other.proposals.approve(
        held.portfolio.portfolio_id,
        waiting.proposal.proposal_id,
        ProposalDecisionRequest(),
        context=operator(),
    )
    assert approved.proposal.status == "applied"
    assert (await other.tagged(held.portfolio.portfolio_id))[0].status is DeploymentStatus.PAUSED


async def test_resume_always_waits_and_live_approval_needs_the_acknowledgement() -> None:
    """Resuming re-arms a sleeve, so a person approves it (live: with i_understand_live)."""
    state = world()
    current = await _started(
        state, mode="live", permissions=ManagerPermissions(may_pause_sleeves=True)
    )
    pid = current.portfolio.portfolio_id
    with pytest.raises(PortfolioConflictError) as running:
        await state.proposals.submit(pid, _sleeve_change(current, "resume_sleeve"), channel="api")
    assert running.value.code == "portfolio_sleeve_not_paused"
    await state.proposals.submit(pid, _sleeve_change(current, "pause_sleeve"), channel="api")
    resume = await state.proposals.submit(
        pid, _sleeve_change(current, "resume_sleeve"), channel="api"
    )
    assert resume.proposal.status == "pending"
    assert "always needs" in str(resume.proposal.approval_reason)
    with pytest.raises(PortfolioLiveAcknowledgementError):
        await state.proposals.approve(
            pid, resume.proposal.proposal_id, ProposalDecisionRequest(), context=operator()
        )
    approved = await state.proposals.approve(
        pid,
        resume.proposal.proposal_id,
        ProposalDecisionRequest(i_understand_live=True),
        context=operator(),
    )
    assert approved.proposal.status == "applied"
    assert {book.status for book in await state.tagged(pid)} == {DeploymentStatus.RUNNING}


async def test_add_sleeve_needs_the_permission_to_propose_and_then_approval() -> None:
    """Without may_propose_sleeves it is refused; with it, a person adds the sleeve."""
    state = world()
    current, _ = await portfolio(state, sleeves=(("BTC-USDC", "0.5"),))
    pid = current.portfolio.portfolio_id
    definition = create_template_strategy(
        product_id="ETH-USDC", timeframe="1h", template="ema-trend"
    )
    extra = await create_strategy_from_definition(state.strategies, definition)
    request = ProposalSubmitRequest.model_validate(
        {
            "revision": current.portfolio.revision,
            "change": {
                "kind": "add_sleeve",
                "strategy_id": str(extra.strategy_id),
                "weight_fraction": "0.2",
            },
            "rationale": "Its walk-forward held for three folds.",
            "evidence": [{"kind": "study", "ref": "sha256:" + "d" * 64}],
        }
    )
    with pytest.raises(PortfolioValidationError) as refused:
        await state.proposals.submit(pid, request, channel="api")
    assert refused.value.code == "portfolio_proposal_not_permitted"
    permitted = world()
    held, _ = await portfolio(
        permitted,
        sleeves=(("BTC-USDC", "0.5"),),
        permissions=ManagerPermissions(may_propose_sleeves=True),
    )
    other = await create_strategy_from_definition(permitted.strategies, definition)
    pending = await permitted.proposals.submit(
        held.portfolio.portfolio_id,
        request.model_copy(
            update={
                "revision": held.portfolio.revision,
                "change": request.change.model_copy(update={"strategy_id": other.strategy_id}),
            }
        ),
        channel="api",
    )
    assert pending.proposal.status == "pending"
    assert pending.proposal.summary.startswith("Add sleeve “")
    assert pending.proposal.summary.endswith("at 20%.")
    approved = await permitted.proposals.approve(
        held.portfolio.portfolio_id,
        pending.proposal.proposal_id,
        ProposalDecisionRequest(),
        context=operator(),
    )
    assert approved.proposal.status == "applied"
    assert [view.sleeve.weight_fraction for view in approved.aggregate.sleeves] == ["0.5", "0.2"]


def test_order_shaped_proposals_are_refused_with_the_manager_boundary() -> None:
    """There is no order proposal: any other kind is refused with the fixed boundary."""
    with pytest.raises(ValidationError) as raised:
        ProposalSubmitRequest.model_validate(
            {
                "revision": 1,
                "change": {"kind": "place_order", "product_id": "BTC-USDC", "side": "buy"},
                "rationale": "Buy the dip.",
            }
        )
    assert ORDER_AUTHORITY_REFUSAL in str(raised.value)


def test_evidence_references_are_validated_per_kind() -> None:
    """Fingerprints, decision refs, and deployment ids each have one shape."""
    base = {
        "revision": 1,
        "change": {"kind": "pause_sleeve", "sleeve_id": "01978a3e-5f2c-7d10-b3a4-0000000000a1"},
        "rationale": "x",
    }
    ProposalSubmitRequest.model_validate(
        {
            **base,
            "evidence": [
                {
                    "kind": "decision",
                    "ref": "01978a3e-5f2c-7d10-b3a4-0000000000b1/BTC-USDC@2026-10-02T11:00:00Z",
                },
                {"kind": "deployment", "ref": "01978a3e-5f2c-7d10-b3a4-0000000000b1"},
            ],
        }
    )
    with pytest.raises(ValidationError):
        ProposalSubmitRequest.model_validate(
            {**base, "evidence": [{"kind": "backtest_result", "ref": "not-a-fingerprint"}]}
        )
    with pytest.raises(ValidationError):
        ProposalSubmitRequest.model_validate({**base, "rationale": "   "})


async def test_stale_revisions_and_expired_proposals_are_refused() -> None:
    """A proposal must name the current revision; unanswered proposals expire after 7 days."""
    state = world()
    current, _ = await portfolio(state)
    pid = current.portfolio.portfolio_id
    with pytest.raises(PortfolioRevisionConflictError):
        await state.proposals.submit(
            pid,
            _rebalance(current, "0.4", "0.4", revision=current.portfolio.revision - 1),
            channel="api",
        )
    long_ago = datetime.now(UTC) - timedelta(days=8)
    old = await state.proposals.submit(
        pid, _rebalance(current, "0.4", "0.4"), channel="api", now=long_ago
    )
    assert old.proposal.status == "pending"
    page = await state.proposals.list_proposals(pid, status=None, limit=10, offset=0)
    assert [item.status for item in page.proposals] == ["expired"]
    with pytest.raises(PortfolioConflictError):
        await state.proposals.approve(
            pid, old.proposal.proposal_id, ProposalDecisionRequest(), context=operator()
        )
    assert await _weights(state, pid) == ["0.5", "0.3"]


async def test_pending_proposals_are_bounded() -> None:
    """At most 20 proposals wait at once; the manager must not flood the queue."""
    state = world()
    current, _ = await portfolio(state)
    pid = current.portfolio.portfolio_id
    for index in range(20):
        weight = Decimal("0.3") + Decimal(index) / Decimal(1000)
        await state.proposals.submit(pid, _rebalance(current, str(weight), "0.3"), channel="api")
    with pytest.raises(PortfolioConflictError) as raised:
        await state.proposals.submit(pid, _rebalance(current, "0.2", "0.3"), channel="api")
    assert raised.value.code == "portfolio_proposal_limit"
