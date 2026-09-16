"""Stop-new-entries, flatten, and managed-shutdown commands distinct from status."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution.models import Deployment, DeploymentStatus, LifecycleCommand
from thytrader.risk.exposure import snapshot_has_residual_exposure

if TYPE_CHECKING:
    from thytrader.execution.models import DeploymentSnapshot

_RUNNING_SLOT = {DeploymentStatus.RUNNING, DeploymentStatus.PAUSED}


def occupies_running_slot(deployment: Deployment) -> bool:
    """True when this book consumes a concurrent running/paused admission slot."""
    return deployment.status in _RUNNING_SLOT


def occupies_risk(snapshot: DeploymentSnapshot) -> bool:
    """True when running, paused, or a stopped book still carries residual exposure."""
    if snapshot.deployment.status in _RUNNING_SLOT:
        return True
    return (
        snapshot.deployment.status is DeploymentStatus.STOPPED
        and snapshot_has_residual_exposure(snapshot)
    )


def entries_allowed(deployment: Deployment) -> bool:
    """True when new risk-increasing entries may be submitted on this book."""
    return (
        deployment.status is DeploymentStatus.RUNNING
        and deployment.lifecycle_command is LifecycleCommand.NONE
        and not deployment.daily_loss_latched
        and not deployment.drawdown_latched
    )


def can_reprice_risk_up(deployment: Deployment) -> bool:
    """Paused, latched, and shutting-down books must not reprice risk-increasing remainders."""
    return entries_allowed(deployment)


def command_for_status(status: DeploymentStatus, *, flatten: bool = False) -> LifecycleCommand:
    """Map an operator status transition onto a lifecycle command."""
    if status is DeploymentStatus.PAUSED:
        return LifecycleCommand.STOP_NEW_ENTRIES
    if status is DeploymentStatus.RUNNING:
        return LifecycleCommand.NONE
    if flatten:
        return LifecycleCommand.FLATTEN
    return LifecycleCommand.MANAGED_SHUTDOWN
