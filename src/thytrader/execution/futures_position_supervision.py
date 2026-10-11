"""Enforce I9 without inventing ownership of unrelated venue inventory."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.audit_events import AuditEventOutcome
from thytrader.execution.audit_scope import record_execution_audit
from thytrader.execution.exit_guards import active_orders
from thytrader.execution.futures_flip_guard import reduce_proven_excess
from thytrader.execution.futures_live import current_live_futures_runtime
from thytrader.execution.futures_venue_check import compare_position
from thytrader.execution.runtime_ops import _pause, cancel_resting_orders
from thytrader.trading.models import is_venue_protection, with_runtime

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
    sample = compare_position(snapshot, None if account is None else account.venue, now=now)
    if sample is not None and sample.matches and sample.book_contracts == 0:
        orphans = tuple(
            order for order in active_orders(snapshot) if is_venue_protection(order.kind)
        )
        if orphans:
            snapshot = await cancel_resting_orders(snapshot, broker=broker, store=store)
            remaining = {order.id for order in active_orders(snapshot)}
            for order in orphans:
                if order.id not in remaining:
                    await record_execution_audit(
                        action="futures_orphan_order_cancelled",
                        outcome=AuditEventOutcome.INFO,
                        detail=(
                            "Flat book and venue: confirmed cancellation of "
                            f"managed protection {order.id}."
                        ),
                        product_id=snapshot.deployment.product_id,
                    )
        if (snapshot.deployment.mismatch_detail or "").startswith(
            "FUTURES_FLAT_CONFIRMATION_PENDING"
        ) and not active_orders(snapshot):
            await store.save_deployment(
                with_runtime(snapshot.deployment, updated_at=now, clear_mismatch=True)
            )
            snapshot = await store.get_deployment(snapshot.deployment.id)
            await record_execution_audit(
                action="futures_flat_confirmed",
                outcome=AuditEventOutcome.SUCCESS,
                detail="A fresh post-fill CFM observation confirms the managed book is flat.",
                product_id=snapshot.deployment.product_id,
            )
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
