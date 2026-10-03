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
