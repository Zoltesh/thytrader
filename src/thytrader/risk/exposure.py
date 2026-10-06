"""Typed exposure helpers for portfolio, product, and risk-bearing snapshots."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.models import (
    DeploymentMode,
    DeploymentStatus,
    IntentPurpose,
    OrderStatus,
    RuntimePhase,
    is_venue_protection,
    resolved_product_id,
    snapshot_positions,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.execution.models import DeploymentSnapshot

_OCCUPIED = {DeploymentStatus.RUNNING, DeploymentStatus.PAUSED}
_DAILY_LOSS = {DeploymentStatus.RUNNING, DeploymentStatus.PAUSED, DeploymentStatus.STOPPED}
_IN_MARKET = {RuntimePhase.OPEN, RuntimePhase.PENDING_ENTRY, RuntimePhase.PENDING_EXIT}
_ACTIVE_ORDER = {OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.UNKNOWN}


def working_entry_notional(snapshot: DeploymentSnapshot, product_id: str) -> Decimal:
    """Sum active entry remainders, excluding verified exit intents and venue protection.

    Missing intent evidence conservatively reserves quote rather than hiding an entry.
    Both buy and sell entries count, since spot shorts also occupy capital.
    """
    total = Decimal("0")
    purposes = {intent.id: intent.purpose for intent in snapshot.intents}
    for order in snapshot.orders:
        if order.status not in _ACTIVE_ORDER or order.price is None:
            continue
        if resolved_product_id(order.product_id, snapshot.deployment) not in {"", product_id}:
            continue
        remaining = order.quantity - order.filled_quantity
        if remaining <= 0:
            continue
        if (
            is_venue_protection(order.kind)
            or purposes.get(order.intent_id, IntentPurpose.ENTRY) is not IntentPurpose.ENTRY
        ):
            continue
        total += remaining * order.price
    return total


def product_exposure(snapshot: DeploymentSnapshot, product_id: str) -> Decimal:
    """Return position cost basis plus any working entry remainder on one product."""
    total = Decimal("0")
    for position in snapshot_positions(snapshot):
        if resolved_product_id(position.product_id, snapshot.deployment) == product_id:
            total += position.quantity * position.entry_price
    return total + working_entry_notional(snapshot, product_id)


def snapshot_has_residual_exposure(snapshot: DeploymentSnapshot) -> bool:
    """True when a snapshot still carries positions, working entries, or in-market phases."""
    if snapshot_positions(snapshot):
        return True
    if snapshot.instrument_runtimes:
        for runtime in snapshot.instrument_runtimes:
            if runtime.phase in _IN_MARKET:
                return True
        for runtime in snapshot.instrument_runtimes:
            if working_entry_notional(snapshot, runtime.product_id) > 0:
                return True
        return False
    if snapshot.deployment.phase in _IN_MARKET:
        return True
    if snapshot.position is not None:
        return True
    return working_entry_notional(snapshot, snapshot.deployment.product_id) > 0


def counts_for_daily_loss(status: DeploymentStatus) -> bool:
    """True when a retained book can still evidence UTC-day loss or a daily-loss latch.

    Stopped flat books stay in this set. Exposure uses ``risk_bearing_snapshots`` instead,
    so a flat stop does not occupy capital or an order-rate slot.
    """
    return status in _DAILY_LOSS


def risk_bearing_snapshots(
    snapshots: Sequence[DeploymentSnapshot], mode: DeploymentMode
) -> tuple[DeploymentSnapshot, ...]:
    """Return running, paused, and stopped snapshots that still bear portfolio risk."""
    return tuple(
        item
        for item in snapshots
        if item.deployment.mode is mode
        and (
            item.deployment.status in _OCCUPIED
            or (
                item.deployment.status is DeploymentStatus.STOPPED
                and snapshot_has_residual_exposure(item)
            )
        )
    )


def daily_loss_snapshots(
    snapshots: Sequence[DeploymentSnapshot], mode: DeploymentMode
) -> tuple[DeploymentSnapshot, ...]:
    """Return same-mode books whose fills and latches still count for UTC-day loss.

    This is wider than exposure: a stopped flat live or paper row keeps its realized
    loss, late fills, and daily-loss latch until an explicit reset or until the row
    itself is deleted. Quote-currency partitioning happens in the breaker, not here.
    """
    return tuple(
        item
        for item in snapshots
        if item.deployment.mode is mode and counts_for_daily_loss(item.deployment.status)
    )
