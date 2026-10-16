"""Explicit candle-free reductions of proven managed live futures inventory.

Reconciliation and cancel confirmation precede submission. An ambiguous reduction is never
cancelled/replaced or resent; the existing persisted intent and FUTURE client scan recover it.
A definite venue rejection pauses the book like a rejected spot exit; identical retries back off
and each retry is a new intent, so a rejection is never mistaken for a submitted reduction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.audit_events import AuditEventOutcome
from thytrader.execution.audit_scope import record_execution_audit
from thytrader.execution.exit_guards import (
    active_orders,
    exit_fill_pending,
    exit_rejection,
    rejection_latched,
)
from thytrader.execution.futures_cancel import cancel_proven_futures_orders
from thytrader.execution.futures_obligations import retain_flat_confirmation
from thytrader.execution.futures_venue_check import compare_position
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.runtime_ops import _pause, _pause_rejected
from thytrader.execution.submit import submit_intent
from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.trading.fill_ledger import unprojected_inventory_products, unsettled_fill_evidence
from thytrader.trading.geometry import exit_order_side
from thytrader.trading.models import (
    DeploymentMode,
    IntentPurpose,
    LifecycleCommand,
    OrderKind,
    OrderStatus,
    RuntimePhase,
    with_runtime,
)

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.execution.broker import Broker
    from thytrader.execution.futures_live import FuturesLiveAccount
    from thytrader.trading.models import DeploymentSnapshot, Position
    from thytrader.trading.store import ExecutionStore


async def reduce_live_futures_position(
    snapshot: DeploymentSnapshot,
    *,
    account: FuturesLiveAccount | None,
    broker: Broker,
    store: ExecutionStore,
    purpose: IntentPurpose,
    now: datetime,
) -> DeploymentSnapshot:
    """Close a known book through a venue-enforced reduction, with no entry/funding gate."""
    if snapshot.deployment.mode is not DeploymentMode.LIVE or not is_futures_product_id(
        snapshot.deployment.product_id
    ):
        raise ValueError("Price-free reductions require a live futures book.")
    if purpose not in {
        IntentPurpose.STOP,
        IntentPurpose.LIQUIDATION,
        IntentPurpose.SIGNAL_EXIT,
        IntentPurpose.TIME_EXIT,
    }:
        raise ValueError("A futures reduction requires an explicit exit purpose.")
    snapshot = await retain_flat_confirmation(snapshot, store=store, now=now)
    snapshot = await reconcile_open_orders(snapshot, broker=broker, store=store)
    # Even a locally flat/unprojected book can retain dangerous owned protection.
    # This helper independently refuses ambiguous marketable exits and unknown ownership.
    snapshot = await cancel_proven_futures_orders(
        snapshot, account=account, broker=broker, store=store, now=now
    )
    snapshot = await reconcile_open_orders(snapshot, broker=broker, store=store)
    if snapshot.position is None:
        return snapshot
    if _unresolved_reduction(snapshot) or active_orders(snapshot):
        return await _pending(snapshot, store=store, now=now)
    if not _reduction_proven(snapshot, account, now):
        return await _pause(
            snapshot,
            store=store,
            detail="FUTURES_REDUCTION_UNPROVEN: inventory changed during cancel confirmation.",
        )
    position = snapshot.position
    rejection = exit_rejection(snapshot, position)
    if rejection is not None and rejection_latched(snapshot, rejection, now=now):
        return await _pause_rejected(snapshot, store=store, rejection=rejection, position=position)
    snapshot = await _pending(snapshot, store=store, now=now)
    order = await submit_intent(
        store=store,
        broker=broker,
        deployment_id=snapshot.deployment.id,
        product_id=snapshot.deployment.product_id,
        purpose=purpose,
        side=exit_order_side(position.side),
        kind=OrderKind.MARKETABLE,
        quantity=position.quantity,
        price=None,
        candle=None,
        decision_at=now,
        idempotency_key=_reduction_key(snapshot, position, purpose),
    )
    if order.status is OrderStatus.REJECTED:
        current = await store.get_deployment(snapshot.deployment.id)
        rejected = exit_rejection(current, position)
        if rejected is not None:
            return await _pause_rejected(
                current, store=store, rejection=rejected, position=position
            )
    mark = None if account is None else account.marks.get(snapshot.deployment.product_id)
    await record_execution_audit(
        action="futures_position_reduction",
        outcome=AuditEventOutcome.INFO,
        detail=(
            f"purpose={purpose.value} quantity={position.quantity} "
            f"mark_for_journal={mark}; "
            "explicit reduction submitted; funding is accrued, not venue-settled."
        ),
        product_id=snapshot.deployment.product_id,
    )
    snapshot = await reconcile_open_orders(
        await store.get_deployment(snapshot.deployment.id),
        broker=broker,
        store=store,
    )
    if snapshot.position is not None:
        return await _pause(
            snapshot,
            store=store,
            detail=(
                "Futures reduction remains unresolved or has residual inventory; "
                "reconcile before another submit."
            ),
        )
    return await _await_flat_confirmation(snapshot, store=store, now=now)


async def _await_flat_confirmation(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    now: datetime,
) -> DeploymentSnapshot:
    """The fill receipt must not masquerade as a subsequent fresh CFM position read."""
    if snapshot.deployment.lifecycle_command is LifecycleCommand.FLATTEN:
        await store.save_deployment(
            with_runtime(
                snapshot.deployment,
                updated_at=now,
                mismatch_detail=(
                    "FUTURES_FLAT_CONFIRMATION_PENDING: "
                    "fills applied; await fresh venue-flat evidence."
                ),
            )
        )
        return await store.get_deployment(snapshot.deployment.id)
    return snapshot


def _reduction_key(snapshot: DeploymentSnapshot, position: Position, purpose: IntentPurpose) -> str:
    """One intent per attempt on this position; a retry after a rejection is a new attempt."""
    base = (
        f"futures-reduction:{snapshot.deployment.id}:{purpose.value}:"
        f"{position.updated_at.isoformat()}"
    )
    attempts = sum(
        1 for intent in snapshot.intents if (intent.idempotency_key or "").startswith(base)
    )
    return base if attempts == 0 else f"{base}:attempt-{attempts + 1}"


def _unresolved_reduction(snapshot: DeploymentSnapshot) -> bool:
    """Do not cancel/recreate a sent reduction or overwrite incomplete fill economics."""
    return (
        exit_fill_pending(snapshot) is not None
        or any(order.kind is OrderKind.MARKETABLE for order in active_orders(snapshot))
        or unsettled_fill_evidence(snapshot)
        or bool(unprojected_inventory_products(snapshot))
    )


def _reduction_proven(
    snapshot: DeploymentSnapshot, account: FuturesLiveAccount | None, now: datetime
) -> bool:
    """Require fresh equal signed contracts and no foreign/unknown product orders."""
    if account is None or account.venue.external_order_products is None:
        return False
    if snapshot.deployment.product_id in account.venue.external_order_products:
        return False
    comparison = compare_position(snapshot, account.venue, now=now)
    return comparison is not None and comparison.matches and comparison.book_contracts != 0


async def _pending(
    snapshot: DeploymentSnapshot, *, store: ExecutionStore, now: datetime
) -> DeploymentSnapshot:
    """Persist reduction-in-progress under the caller's revision/lease fence."""
    await store.save_deployment(
        with_runtime(snapshot.deployment, updated_at=now, phase=RuntimePhase.PENDING_EXIT)
    )
    return await store.get_deployment(snapshot.deployment.id)
