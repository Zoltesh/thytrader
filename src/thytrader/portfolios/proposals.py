"""Manager proposals: typed changes, cited evidence, and the approval rules (ADR 0091).

The manager agent (Hermes or Claude through the ``thytrader-portfolio`` skill, later an
in-app agent) runs its loop outside ThyTrader. It reads the briefing, decides, and submits
a proposal: rebalance the weights, pause or resume one sleeve, or add a sleeve from a
strategy, always with a rationale and the evidence it cites. ThyTrader never lets a
proposal place an order: there is no such change kind, and strategies place every trade.

State machine::

    submit ──► applied (auto-applied: within the manager's permissions and bounds)
       │
       └─────► pending ──► applied   (a person approved; the change was made)
                      ├──► declined  (a person declined)
                      ├──► failed    (approved, but the change no longer applies)
                      └──► expired   (nobody decided within 7 days)

Only ``pending`` proposals can be approved or declined; every other status is terminal.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import re
from typing import TYPE_CHECKING, Annotated, Final, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_serializer,
    field_validator,
    model_validator,
)

from thytrader.decimal_text import canonical_decimal
from thytrader.portfolios.errors import PortfolioValidationError
from thytrader.portfolios.models import PortfolioAggregate, WeightAssignment, require_utc, utc_text
from thytrader.portfolios.values import (
    ReserveFractionText,
    RevisionNumber,
    SleeveNoteText,
    WeightFractionText,
)
from thytrader.portfolios.vocabulary import (
    MAX_RATIONALE_LENGTH,
    MAX_SLEEVES,
    MAX_SUMMARY_LENGTH,
    JournalChannel,
)

if TYPE_CHECKING:
    from thytrader.portfolios.models import JournalEntry
    from thytrader.portfolios.rules import MutationPlan

ProposalKind = Literal["rebalance", "pause_sleeve", "resume_sleeve", "add_sleeve"]
PROPOSAL_KINDS: Final[tuple[ProposalKind, ...]] = (
    "rebalance",
    "pause_sleeve",
    "resume_sleeve",
    "add_sleeve",
)
ProposalStatus = Literal["pending", "applied", "declined", "failed", "expired"]
PROPOSAL_STATUSES: Final[tuple[ProposalStatus, ...]] = (
    "pending",
    "applied",
    "declined",
    "failed",
    "expired",
)
EvidenceKind = Literal["backtest_result", "portfolio_backtest", "study", "decision", "deployment"]
DecidedBy = Literal["operator", "manager", "system"]
PROPOSAL_TTL: Final = timedelta(days=7)
REBALANCE_BUDGET_WINDOW: Final = timedelta(days=7)
MAX_EVIDENCE: Final = 20
ORDER_AUTHORITY_REFUSAL: Final = (
    "The manager never places orders; strategies place every trade. A proposal may only "
    "rebalance weights, pause or resume a sleeve, or add a sleeve (ADR 0091)."
)

_FINGERPRINT = re.compile(r"^sha256:[0-9a-f]{64}$")
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_DECISION_REF = re.compile(
    rf"^{_UUID}/[A-Z0-9]{{1,16}}-[A-Z0-9]{{1,16}}@\d{{4}}-\d{{2}}-\d{{2}}T\d{{2}}:\d{{2}}:\d{{2}}Z$"
)
_DEPLOYMENT_REF = re.compile(rf"^{_UUID}$")
_ZERO = Decimal(0)


def _rationale(value: str) -> str:
    """Trim a rationale and require 1-2000 characters of plain text."""
    stripped = value.strip()
    if not stripped:
        raise ValueError("rationale must not be blank: say why, citing the evidence")
    if len(stripped) > MAX_RATIONALE_LENGTH:
        raise ValueError(f"rationale allows at most {MAX_RATIONALE_LENGTH} characters")
    return stripped


def _decision_note(value: str) -> str:
    """Trim an approve/decline note (at most 500 characters)."""
    stripped = value.strip()
    if len(stripped) > MAX_SUMMARY_LENGTH:
        raise ValueError(f"note allows at most {MAX_SUMMARY_LENGTH} characters")
    return stripped


RationaleText = Annotated[str, Field(strict=True), AfterValidator(_rationale)]
DecisionNoteText = Annotated[str, Field(strict=True), AfterValidator(_decision_note)]


class _FrozenModel(BaseModel):
    """Reject unknown fields and prevent mutation after validation."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ProposalEvidence(_FrozenModel):
    """One piece of evidence a proposal cites.

    ``ref`` is a result fingerprint (``backtest_result``, ``portfolio_backtest``,
    ``study``: ``sha256:…``), a decision row (``decision``:
    ``<deployment_id>/<product_id>@<bar_starts_at>``, exactly as the briefing prints
    it), or a deployment id (``deployment``). ThyTrader records what was cited; it does
    not re-run the evidence.
    """

    kind: EvidenceKind
    ref: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=280)

    @model_validator(mode="after")
    def require_ref_shape(self) -> Self:
        """Validate the reference format for its kind."""
        if self.kind in {"backtest_result", "portfolio_backtest", "study"}:
            if _FINGERPRINT.fullmatch(self.ref) is None:
                raise ValueError(f"{self.kind} evidence must be a sha256: fingerprint")
        elif self.kind == "decision":
            if _DECISION_REF.fullmatch(self.ref) is None:
                raise ValueError(
                    "decision evidence must be <deployment_id>/<product_id>@<bar_starts_at>, "
                    "as printed by the briefing"
                )
        elif _DEPLOYMENT_REF.fullmatch(self.ref) is None:
            raise ValueError("deployment evidence must be a deployment id")
        return self


class RebalanceChange(_FrozenModel):
    """New weights for every sleeve (and optionally the cash reserve)."""

    kind: Literal["rebalance"] = "rebalance"
    weights: tuple[WeightAssignment, ...] = Field(min_length=1, max_length=MAX_SLEEVES)
    cash_reserve_fraction: ReserveFractionText | None = None


class PauseSleeveChange(_FrozenModel):
    """Pause one running sleeve (risk-reducing: no new entries; exits continue)."""

    kind: Literal["pause_sleeve"] = "pause_sleeve"
    sleeve_id: UUID


class ResumeSleeveChange(_FrozenModel):
    """Resume one paused sleeve. Always needs approval."""

    kind: Literal["resume_sleeve"] = "resume_sleeve"
    sleeve_id: UUID


class AddSleeveChange(_FrozenModel):
    """Add a strategy as a new sleeve. Always needs approval."""

    kind: Literal["add_sleeve"] = "add_sleeve"
    strategy_id: UUID
    weight_fraction: WeightFractionText
    note: SleeveNoteText | None = None


ProposalChange = Annotated[
    RebalanceChange | PauseSleeveChange | ResumeSleeveChange | AddSleeveChange,
    Field(discriminator="kind"),
]


class ProposalSubmitRequest(_FrozenModel):
    """One proposal from the manager agent, planned against the portfolio's revision."""

    revision: RevisionNumber
    change: ProposalChange
    rationale: RationaleText
    evidence: tuple[ProposalEvidence, ...] = Field(default=(), max_length=MAX_EVIDENCE)

    @model_validator(mode="before")
    @classmethod
    def refuse_order_authority(cls, data: object) -> object:
        """Answer any order-shaped change with the manager's fixed boundary."""
        if isinstance(data, dict):
            change = data.get("change")
            kind = change.get("kind") if isinstance(change, dict) else None
            if isinstance(kind, str) and kind not in PROPOSAL_KINDS:
                raise ValueError(f"Unknown proposal kind {kind!r}. {ORDER_AUTHORITY_REFUSAL}")
        return data


class ProposalDecisionRequest(_FrozenModel):
    """Approve or decline one pending proposal.

    Approving a resume on a live portfolio, or adding a sleeve to a deployed live
    portfolio (which starts it), re-arms live orders and needs ``i_understand_live``.
    """

    note: DecisionNoteText | None = None
    i_understand_live: StrictBool = False


class Proposal(_FrozenModel):
    """One stored proposal and its outcome."""

    proposal_id: UUID
    portfolio_id: UUID
    kind: ProposalKind
    status: ProposalStatus
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY_LENGTH)
    rationale: str = Field(min_length=1, max_length=MAX_RATIONALE_LENGTH)
    change: ProposalChange
    evidence: tuple[ProposalEvidence, ...] = ()
    base_revision: int = Field(ge=1)
    submitted_by: Literal["manager", "operator"] = "manager"
    channel: JournalChannel
    approval_reason: str | None = Field(default=None, max_length=MAX_SUMMARY_LENGTH)
    weight_moved: str | None = None
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None
    decided_by: DecidedBy | None = None
    decision_note: str | None = Field(default=None, max_length=MAX_SUMMARY_LENGTH)
    auto_applied: bool = False
    applied_revision: int | None = None
    failure_code: str | None = Field(default=None, max_length=64)
    failure_message: str | None = Field(default=None, max_length=MAX_SUMMARY_LENGTH)

    @field_validator("created_at", "expires_at", "decided_at")
    @classmethod
    def require_utc_instants(cls, value: datetime | None) -> datetime | None:
        """Keep proposal instants timezone-aware UTC."""
        return None if value is None else require_utc(value)

    @field_serializer("created_at", "expires_at", "decided_at", when_used="json")
    def serialize_instants(self, value: datetime | None) -> str | None:
        """Render instants with a Z suffix."""
        return None if value is None else utc_text(value)


@dataclass(frozen=True, slots=True)
class ProposalPage:
    """One newest-first page of proposals plus the total count."""

    proposals: tuple[Proposal, ...]
    total: int


@dataclass(frozen=True, slots=True)
class ProposalSettlement:
    """What a store writes in one transaction when a proposal is created or decided.

    ``plan`` is the portfolio mutation an applied rebalance or add-sleeve makes (None for
    pause/resume, whose deployment change happens in the execution store, and for
    declines and failures).
    """

    proposal: Proposal
    plan: MutationPlan | None
    journal: tuple[JournalEntry, ...]


@dataclass(frozen=True, slots=True)
class ApprovalRequirement:
    """Whether a proposal auto-applies, and why a person must otherwise approve it."""

    auto_apply: bool
    reason: str | None
    weight_moved: Decimal | None = None


def weight_moved(aggregate: PortfolioAggregate, change: RebalanceChange) -> Decimal:
    """Weight a rebalance moves: the larger of total increases and total decreases.

    Moving 10% from one sleeve to another moves 10%; raising one sleeve by 5% out of
    unallocated cash moves 5%. Every sleeve must be named exactly once.
    """
    current = {
        view.sleeve.sleeve_id: Decimal(view.sleeve.weight_fraction) for view in aggregate.sleeves
    }
    proposed = {item.sleeve_id: Decimal(item.weight_fraction) for item in change.weights}
    if len(proposed) != len(change.weights) or set(proposed) != set(current):
        raise PortfolioValidationError(
            "portfolio_weights_incomplete",
            "A rebalance must name every sleeve exactly once (propose removing a sleeve "
            "by pausing it instead).",
        )
    increases = sum((max(_ZERO, proposed[key] - current[key]) for key in current), _ZERO)
    decreases = sum((max(_ZERO, current[key] - proposed[key]) for key in current), _ZERO)
    return max(increases, decreases)


def approval_requirement(
    aggregate: PortfolioAggregate,
    change: RebalanceChange | PauseSleeveChange | ResumeSleeveChange | AddSleeveChange,
    *,
    moved_this_week: Decimal,
) -> ApprovalRequirement:
    """Decide whether the manager's permissions and bounds let this change auto-apply.

    * pause a sleeve: auto when ``may_pause_sleeves`` (risk-reducing, paper or live);
    * rebalance: auto only on a paper portfolio with ``may_rebalance`` and when the move
      fits what is left of the rolling 7-day ``max_weight_change_per_week`` budget
      (auto-applied rebalances only); a live rebalance moves real capital, so it always
      waits for a person;
    * resume a sleeve and add a sleeve: always need approval.
    """
    permissions = aggregate.portfolio.manager.permissions
    if isinstance(change, PauseSleeveChange):
        if permissions.may_pause_sleeves:
            return ApprovalRequirement(auto_apply=True, reason=None)
        return ApprovalRequirement(
            auto_apply=False, reason="Pausing a sleeve needs approval: may_pause_sleeves is off."
        )
    if isinstance(change, ResumeSleeveChange):
        return ApprovalRequirement(
            auto_apply=False, reason="Resuming a sleeve always needs a person's approval."
        )
    if isinstance(change, AddSleeveChange):
        return ApprovalRequirement(
            auto_apply=False, reason="Adding a sleeve always needs a person's approval."
        )
    moved = weight_moved(aggregate, change)
    return _rebalance_requirement(aggregate, moved=moved, moved_this_week=moved_this_week)


def _rebalance_requirement(
    aggregate: PortfolioAggregate, *, moved: Decimal, moved_this_week: Decimal
) -> ApprovalRequirement:
    """Auto-apply a paper rebalance inside the weekly budget; everything else waits."""
    portfolio = aggregate.portfolio
    permissions = portfolio.manager.permissions
    if portfolio.mode == "live":
        return ApprovalRequirement(
            auto_apply=False,
            reason="This is a live portfolio: a rebalance moves real capital and needs approval.",
            weight_moved=moved,
        )
    if not permissions.may_rebalance:
        return ApprovalRequirement(
            auto_apply=False,
            reason="Rebalancing needs approval: may_rebalance is off.",
            weight_moved=moved,
        )
    budget = Decimal(permissions.max_weight_change_per_week)
    remaining = max(_ZERO, budget - moved_this_week)
    if moved > remaining:
        return ApprovalRequirement(
            auto_apply=False,
            reason=(
                f"Moves {percent(moved)}; only {percent(remaining)} of the "
                f"{percent(budget)} weekly auto-apply budget is left."
            ),
            weight_moved=moved,
        )
    return ApprovalRequirement(auto_apply=True, reason=None, weight_moved=moved)


def percent(fraction: Decimal) -> str:
    """Render a fraction as a percent without trailing zeros (``0.1`` → ``10%``)."""
    return f"{canonical_decimal(fraction * 100)}%"


def proposal_summary(
    aggregate: PortfolioAggregate,
    change: RebalanceChange | PauseSleeveChange | ResumeSleeveChange | AddSleeveChange,
    *,
    strategy_name: str | None = None,
) -> str:
    """One line naming what the proposal would change."""
    names = {view.sleeve.sleeve_id: view.strategy.name for view in aggregate.sleeves}
    if isinstance(change, PauseSleeveChange):
        return f"Pause sleeve “{names.get(change.sleeve_id, 'unknown sleeve')}”."
    if isinstance(change, ResumeSleeveChange):
        return f"Resume sleeve “{names.get(change.sleeve_id, 'unknown sleeve')}”."
    if isinstance(change, AddSleeveChange):
        name = strategy_name or str(change.strategy_id)
        return f"Add sleeve “{name}” at {percent(Decimal(change.weight_fraction))}."
    current = {view.sleeve.sleeve_id: view.sleeve.weight_fraction for view in aggregate.sleeves}
    moves = [
        f"{names.get(item.sleeve_id, 'unknown')} {percent(Decimal(current[item.sleeve_id]))} → "
        f"{percent(Decimal(item.weight_fraction))}"
        for item in change.weights
        if item.sleeve_id in current and current[item.sleeve_id] != item.weight_fraction
    ]
    if change.cash_reserve_fraction is not None and (
        change.cash_reserve_fraction != aggregate.portfolio.cash_reserve_fraction
    ):
        moves.append(
            f"cash reserve {percent(Decimal(aggregate.portfolio.cash_reserve_fraction))} → "
            f"{percent(Decimal(change.cash_reserve_fraction))}"
        )
    text = "; ".join(moves) if moves else "no weight changes"
    return f"Rebalance: {text}."[:MAX_SUMMARY_LENGTH]


def expiry(created_at: datetime) -> datetime:
    """When a pending proposal expires unanswered."""
    return created_at + PROPOSAL_TTL


def canonical_weight_text(value: Decimal) -> str:
    """Canonical fraction text for ``weight_moved``."""
    return canonical_decimal(value)
