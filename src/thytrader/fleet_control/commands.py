"""Revision-fenced lifecycle command semantics shared by fleet persistence backends."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.fleet_control.models import (
    FleetAction,
    FleetModeScope,
    FleetTargetStatus,
    TargetResult,
    VenueEffect,
)
from thytrader.trading.lifecycle import command_for_status
from thytrader.trading.models import DeploymentStatus, with_runtime

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.fleet_control.models import ExpectedTarget
    from thytrader.trading.models import Deployment


def confirmed_command(
    row: Deployment | None,
    expected: ExpectedTarget,
    action: FleetAction,
    mode: FleetModeScope,
    now: datetime,
) -> tuple[Deployment | None, TargetResult]:
    """Plan a command only from a row locked at the confirmed revision.

    The persistence backend must couple the returned mutation and receipt in
    one transaction. Coincidental current state is not evidence of a replay.
    """
    if row is None or (mode is not FleetModeScope.ALL and row.mode.value != mode.value):
        return None, TargetResult(
            expected.deployment_id,
            expected.revision,
            FleetTargetStatus.FAILED,
            "Deployment is not in the confirmed mode scope.",
            VenueEffect.NONE,
        )
    if row.revision != expected.revision:
        return None, TargetResult(
            row.id,
            expected.revision,
            FleetTargetStatus.REVISION_CONFLICT,
            f"Revision is {row.revision}, not confirmed {expected.revision}.",
            VenueEffect.NONE,
        )
    flatten = action is FleetAction.FLATTEN
    command = command_for_status(DeploymentStatus.STOPPED, flatten=flatten)
    effect = VenueEffect.ASYNC_FLATTEN if flatten else VenueEffect.ASYNC_MANAGED_SHUTDOWN
    if row.status is DeploymentStatus.STOPPED and row.lifecycle_command is command:
        return None, TargetResult(
            row.id,
            expected.revision,
            FleetTargetStatus.UNCHANGED,
            "Confirmed revision already has this command; not a causal replay.",
            VenueEffect.NONE,
        )
    updated = with_runtime(
        row,
        updated_at=now,
        status=DeploymentStatus.STOPPED,
        lifecycle_command=command,
        clear_mismatch=True,
    )
    return updated, TargetResult(
        row.id,
        expected.revision,
        FleetTargetStatus.COMMAND_RECORDED,
        "Lifecycle command and causal receipt recorded. Worker completion is asynchronous.",
        effect,
    )
