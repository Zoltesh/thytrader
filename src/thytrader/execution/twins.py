"""Explicit one-to-one paper/live comparison links, independent of trading state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 - dataclass field type.
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003 - dataclass field type.

from thytrader.execution.models import Deployment, DeploymentKind, DeploymentMode
from thytrader.strategies.models import canonical_strategy_bytes, strategy_fingerprint

if TYPE_CHECKING:
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.strategies.snapshots import StrategySnapshot, StrategySnapshotStore


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


def comparable_twins(
    first: Deployment,
    second: Deployment,
    *,
    snapshots: tuple[StrategySnapshot, StrategySnapshot] | None = None,
) -> tuple[Deployment, Deployment]:
    """Validate immutable comparison facts and return paper then live."""
    if first.id == second.id or first.mode == second.mode:
        raise TwinValidationError("Twin links require one paper bot and one live bot.")
    if first.kind is not DeploymentKind.STRATEGY or second.kind is not DeploymentKind.STRATEGY:
        raise TwinValidationError("Only strategy bots can be linked as twins.")
    if not first.strategy_fingerprint or not second.strategy_fingerprint:
        raise TwinValidationError("Twins must bind strategy snapshots.")
    if first.product_id != second.product_id or first.timeframe != second.timeframe:
        raise TwinValidationError("Twins must use the same market and timeframe.")
    if first.strategy_fingerprint != second.strategy_fingerprint:
        _require_same_rules(first, second, snapshots)
    return (first, second) if first.mode is DeploymentMode.PAPER else (second, first)


async def load_twin_snapshots(
    first: Deployment,
    second: Deployment,
    publications: StrategySnapshotStore,
) -> tuple[StrategySnapshot, StrategySnapshot] | None:
    """Load the pinned rules only when different snapshot identities need proof."""
    if first.strategy_fingerprint == second.strategy_fingerprint:
        return None
    if not first.strategy_fingerprint or not second.strategy_fingerprint:
        raise TwinValidationError("Twins must bind strategy snapshots.")
    return (
        await publications.load(first.strategy_fingerprint),
        await publications.load(second.strategy_fingerprint),
    )


def _require_same_rules(
    first: Deployment,
    second: Deployment,
    snapshots: tuple[StrategySnapshot, StrategySnapshot] | None,
) -> None:
    """Verify proof belongs to these immutable bindings and all trading fields agree."""
    if snapshots is None or tuple(item.strategy_fingerprint for item in snapshots) != (
        first.strategy_fingerprint,
        second.strategy_fingerprint,
    ):
        raise TwinValidationError("Different snapshots require verified identical trading rules.")
    if any(
        strategy_fingerprint(item.definition) != item.strategy_fingerprint for item in snapshots
    ):
        raise TwinValidationError("Twin snapshot proof does not match its content fingerprint.")
    if not _same_trading_rules(snapshots[0].definition, snapshots[1].definition):
        raise TwinValidationError("Twins must run identical snapshotted trading rules.")


def _same_trading_rules(first: StrategyDefinition, second: StrategyDefinition) -> bool:
    """Ignore only identity and annotations; retain every strategy and execution field."""
    aligned = second.model_copy(
        update={
            "strategy_id": first.strategy_id,
            "name": first.name,
            "description": first.description,
            "created_at": first.created_at,
            "metadata": first.metadata,
        }
    )
    return canonical_strategy_bytes(first) == canonical_strategy_bytes(aligned)
