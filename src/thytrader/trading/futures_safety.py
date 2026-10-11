"""Independent durable futures workflow duties, never diagnostic-string authority."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.trading.models import DeploymentMode, LifecycleCommand

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.trading.models import Deployment


def require_futures_flat_confirmation(deployment: Deployment, *, after: datetime) -> Deployment:
    """Retain a monotone lower bound, not a manufactured successful venue observation.

    Call before cancellation/submission and inside canonical fill projection. A late fill
    atomically rearms verification even if an earlier flat observation was already accepted.
    """
    if (
        deployment.mode is not DeploymentMode.LIVE
        or not is_futures_product_id(deployment.product_id)
        or (
            deployment.lifecycle_command is not LifecycleCommand.FLATTEN
            and not deployment.futures_derisk_pending
        )
    ):
        return deployment
    previous = deployment.futures_flat_confirmation_after
    return replace(
        deployment,
        futures_flat_confirmation_after=max(previous, after) if previous is not None else after,
    )
