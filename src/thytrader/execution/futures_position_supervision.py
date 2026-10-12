"""Enforce I9 without inventing ownership of unrelated venue inventory."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from thytrader.audit_events import AuditEventOutcome
from thytrader.execution.audit_scope import record_execution_audit
from thytrader.execution.exit_guards import active_orders
from thytrader.execution.futures_cancel import cancel_proven_futures_orders, cancellation_proven
from thytrader.execution.futures_flip_guard import reduce_proven_excess
from thytrader.execution.futures_live import current_live_futures_runtime
from thytrader.execution.futures_venue_check import compare_position
from thytrader.execution.runtime_ops import _pause
from thytrader.trading.fill_ledger import unprojected_inventory_products
from thytrader.trading.futures_repair import excess_quantity, managed_signed_base
from thytrader.trading.models import is_venue_protection

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.execution.broker import Broker
    from thytrader.execution.futures_live import FuturesLiveAccount
    from thytrader.trading.models import DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore


async def enforce_position_check(
    snapshot: DeploymentSnapshot,
    *,
    account: FuturesLiveAccount | None,
    broker: Broker,
    store: ExecutionStore,
    now: datetime,
) -> DeploymentSnapshot:
    """Persist a mismatch after two comparable reads; never auto-clear an existing pause."""
    runtime = current_live_futures_runtime()
    if runtime is None:
        return snapshot
    # Cancellation has its own ownership authority. A partial stop must not prevent
    # the quiescence required to begin the two-observation reduction proof.
    snapshot = await _cancel_displaced_protection(
        snapshot, account=account, broker=broker, store=store, now=now
    )
    sample = compare_position(snapshot, None if account is None else account.venue, now=now)
    if sample is not None and sample.matches and sample.book_contracts == 0:
        snapshot = await _complete_flat_obligations(snapshot, account=account, store=store, now=now)
    if not runtime.confirmations.confirm(snapshot.deployment.id, sample) or sample is None:
        return snapshot
    if account is not None:
        snapshot = await reduce_proven_excess(
            snapshot,
            account=account,
            broker=broker,
            store=store,
            now=now,
        )
    return await _pause(snapshot, store=store, detail=sample.detail)


async def _cancel_displaced_protection(
    snapshot: DeploymentSnapshot,
    *,
    account: FuturesLiveAccount | None,
    broker: Broker,
    store: ExecutionStore,
    now: datetime,
) -> DeploymentSnapshot:
    """Cancel owned orphan/flip protection, never a matching book's valid resting stop."""
    orders = active_orders(snapshot)
    if (
        not orders
        or any(not is_venue_protection(order.kind) for order in orders)
        or not cancellation_proven(snapshot, account, now)
    ):
        return snapshot
    managed = managed_signed_base(snapshot)
    if managed is None or (managed != 0 and excess_quantity(snapshot, managed) == 0):
        return snapshot
    flat = snapshot.position is None and managed == 0
    snapshot = await cancel_proven_futures_orders(
        snapshot, account=account, broker=broker, store=store, now=now
    )
    remaining = {order.id for order in active_orders(snapshot)}
    if flat:
        for order in orders:
            if order.id not in remaining:
                await record_execution_audit(
                    action="futures_orphan_order_cancelled",
                    outcome=AuditEventOutcome.INFO,
                    detail=f"Confirmed cancellation of managed orphan protection {order.id}.",
                    product_id=snapshot.deployment.product_id,
                )
    return snapshot


async def _complete_flat_obligations(
    snapshot: DeploymentSnapshot,
    *,
    account: FuturesLiveAccount | None,
    store: ExecutionStore,
    now: datetime,
) -> DeploymentSnapshot:
    """Clear duties together only after distinct complete post-fill venue-flat evidence."""
    after = snapshot.deployment.futures_flat_confirmation_after
    if after is None or account is None:
        return snapshot
    # Cancels above may have ingested a racing fill: re-evaluate, never reuse their old proof.
    sample = compare_position(snapshot, account.venue, now=now)
    if (
        sample is None
        or not sample.matches
        or sample.book_contracts != 0
        or sample.observed_at <= after
        or active_orders(snapshot)
        or unprojected_inventory_products(snapshot)
        or account.venue.external_order_products is None
        or snapshot.deployment.product_id in account.venue.external_order_products
    ):
        return snapshot
    detail = snapshot.deployment.mismatch_detail
    if (detail or "").startswith("FUTURES_FLAT_CONFIRMATION_PENDING"):
        detail = None
    await store.save_deployment(
        replace(
            snapshot.deployment,
            updated_at=now,
            futures_derisk_pending=False,
            futures_flat_confirmation_after=None,
            mismatch_detail=detail,
        )
    )
    await record_execution_audit(
        action="futures_flat_confirmed",
        outcome=AuditEventOutcome.SUCCESS,
        detail="A fresh post-fill CFM observation confirms the managed book is flat.",
        product_id=snapshot.deployment.product_id,
    )
    return await store.get_deployment(snapshot.deployment.id)
