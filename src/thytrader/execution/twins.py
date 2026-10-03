"""Explicit one-to-one paper/live comparison links, independent of trading state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 - dataclass field type.
from uuid import UUID  # noqa: TC003 - dataclass field type.

from thytrader.execution.models import Deployment, DeploymentKind, DeploymentMode


class TwinValidationError(ValueError):
    """Reject books that cannot be compared under the same strategy semantics."""


class TwinConflictError(ValueError):
    """Reject an occupied book or an unlink whose expected partner has changed."""


@dataclass(frozen=True, slots=True)
class DeploymentTwinLink:
    """Persist the intended pair without modifying either deployment or its lease."""

    paper_deployment_id: UUID
    live_deployment_id: UUID
    linked_at: datetime

    def counterpart(self, deployment_id: UUID) -> UUID:
        """Return the opposite member; reject ids outside this pair."""
        if deployment_id == self.paper_deployment_id:
            return self.live_deployment_id
        if deployment_id == self.live_deployment_id:
            return self.paper_deployment_id
        raise TwinValidationError("Deployment is not a member of this twin link.")


def comparable_twins(first: Deployment, second: Deployment) -> tuple[Deployment, Deployment]:
    """Validate immutable comparison facts and return paper then live."""
    if first.id == second.id or first.mode == second.mode:
        raise TwinValidationError("Twin links require one paper bot and one live bot.")
    if first.kind is not DeploymentKind.STRATEGY or second.kind is not DeploymentKind.STRATEGY:
        raise TwinValidationError("Only strategy bots can be linked as twins.")
    if not first.strategy_fingerprint or first.strategy_fingerprint != second.strategy_fingerprint:
        raise TwinValidationError("Twins must bind the same strategy snapshot.")
    if first.product_id != second.product_id or first.timeframe != second.timeframe:
        raise TwinValidationError("Twins must use the same market and timeframe.")
    return (first, second) if first.mode is DeploymentMode.PAPER else (second, first)
