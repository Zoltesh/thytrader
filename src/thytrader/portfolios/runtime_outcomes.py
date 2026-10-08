"""Portfolio action outcomes and the journal and audit text that records them.

Each start, pause, resume, or stop yields one outcome per sleeve book. Journal summaries
tally those outcomes, audit details carry no cash, quantities, or secrets, and journal
instants are truncated to the millisecond a UUIDv7 encodes and PostgreSQL round-trips.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from thytrader.portfolios.models import MutationContext, utc_millisecond
from thytrader.trading.models import Deployment, DeploymentStatus

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.portfolios.models import PortfolioAggregate, SleeveView


SleeveOutcomeKind = Literal[
    "started", "attached", "paused", "resumed", "stopped", "unchanged", "failed"
]
PortfolioAction = Literal["start", "pause", "resume", "stop"]


@dataclass(frozen=True, slots=True)
class SleeveOutcome:
    """What one portfolio action did to one sleeve's book."""

    sleeve_id: UUID | None
    strategy_id: UUID | None
    strategy_name: str
    outcome: SleeveOutcomeKind
    deployment_id: UUID | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class PortfolioActionResult:
    """One start, pause, resume, or stop and its per-sleeve outcomes."""

    action: PortfolioAction
    outcomes: tuple[SleeveOutcome, ...]


def _outcome(
    name: str,
    view: SleeveView | None,
    deployment: Deployment | None,
    outcome: SleeveOutcomeKind,
    message: str | None,
) -> SleeveOutcome:
    """One sleeve outcome."""
    return SleeveOutcome(
        sleeve_id=None if view is None else view.sleeve.sleeve_id,
        strategy_id=(
            view.sleeve.strategy_id
            if view is not None
            else (None if deployment is None else deployment.strategy_id)
        ),
        strategy_name=name,
        outcome=outcome,
        deployment_id=None if deployment is None else deployment.id,
        message=message,
    )


_AUDIT_ACTIONS: dict[DeploymentStatus, str] = {
    DeploymentStatus.PAUSED: "portfolio_pause",
    DeploymentStatus.RUNNING: "portfolio_resume",
    DeploymentStatus.STOPPED: "portfolio_stop",
}
_VERBS: dict[PortfolioAction, str] = {
    "start": "Started",
    "pause": "Paused",
    "resume": "Resumed",
    "stop": "Stopped",
}


def _action_summary(
    aggregate: PortfolioAggregate, result: PortfolioActionResult, *, sleeve_id: UUID | None
) -> str:
    """One journal line for a portfolio or sleeve action."""
    verb = _VERBS[result.action]
    counts: dict[str, int] = {}
    for item in result.outcomes:
        counts[item.outcome] = counts.get(item.outcome, 0) + 1
    tally = ", ".join(f"{count} {name}" for name, count in sorted(counts.items()))
    mode = aggregate.portfolio.mode
    if sleeve_id is not None and result.outcomes:
        return f"{verb} {mode} sleeve “{result.outcomes[0].strategy_name}” ({tally})."
    return f"{verb} {mode} portfolio “{aggregate.portfolio.name}” ({tally})."


def _deployment_audit(deployment: Deployment) -> str:
    """Audit detail without cash, quantities, or secrets."""
    return (
        f"deployment_id={deployment.id} mode={deployment.mode.value} "
        f"status={deployment.status.value} fingerprint={deployment.strategy_fingerprint}"
    )


def _millisecond(context: MutationContext) -> MutationContext:
    """Journal instants at the millisecond a UUIDv7 encodes and PostgreSQL round-trips."""
    return MutationContext(
        actor=context.actor,
        channel=context.channel,
        occurred_at=utc_millisecond(context.occurred_at),
    )
