"""Persist futures completion duties before reconciliation can remove their trigger."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution.exit_guards import active_orders
from thytrader.trading.fill_ledger import unprojected_inventory_products, unsettled_fill_evidence
from thytrader.trading.futures_safety import require_futures_flat_confirmation

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.trading.models import DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore


async def retain_flat_confirmation(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    now: datetime,
) -> DeploymentSnapshot:
    """Latch once before cancel/submit/fill ingestion; only fresh venue proof discharges it."""
    if snapshot.deployment.futures_flat_confirmation_after is not None:
        return snapshot
    if not (
        snapshot.position is not None
        or active_orders(snapshot)
        or unsettled_fill_evidence(snapshot)
        or unprojected_inventory_products(snapshot)
        or snapshot.deployment.futures_derisk_pending
    ):
        return snapshot
    deployment = require_futures_flat_confirmation(snapshot.deployment, after=now)
    if deployment != snapshot.deployment:
        await store.save_deployment(deployment)
        return await store.get_deployment(deployment.id)
    return snapshot
