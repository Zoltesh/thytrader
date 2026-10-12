"""Narrow managed-order cancellation authority, not permission to submit a reduction."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, DecimalException, Inexact, localcontext
from typing import TYPE_CHECKING

from thytrader.execution.broker import CANCEL_PENDING_REASON, BrokerError
from thytrader.execution.exit_guards import active_orders, cancel_pending
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.trading.fill_ledger import unsettled_fill_evidence
from thytrader.trading.futures_repair import managed_signed_base
from thytrader.trading.models import DeploymentMode, OrderKind, OrderStatus

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.execution.broker import Broker
    from thytrader.execution.futures_live import FuturesLiveAccount
    from thytrader.trading.models import DeploymentSnapshot, Order
    from thytrader.trading.store import ExecutionStore


def _owned_order(snapshot: DeploymentSnapshot, order: Order) -> bool:
    """Require the persisted intent's exact managed product/client/deployment identity."""
    intent = next((item for item in snapshot.intents if item.id == order.intent_id), None)
    return (
        intent is not None
        and intent.deployment_id == order.deployment_id == snapshot.deployment.id
        and intent.product_id == order.product_id == snapshot.deployment.product_id
        and intent.client_order_id == order.client_order_id
        and order.client_order_id.startswith(f"{snapshot.deployment.id}:{intent.purpose.value}:")
    )


def cancellation_proven(
    snapshot: DeploymentSnapshot,
    account: FuturesLiveAccount | None,
    now: datetime,
) -> bool:
    """Prove all signed venue inventory is ours without requiring an unfilled projection.

    A partially executed OPEN stop is cancellable, but unsettled economics, unknown orders,
    foreign inventory and stale observations are not authority. Every POST still requires
    the separate strict I9 comparison after cancellation-race fills have been ingested.
    """
    if (
        snapshot.deployment.mode is not DeploymentMode.LIVE
        or not is_futures_product_id(snapshot.deployment.product_id)
        or not snapshot.accounting_complete
        or account is None
        or not account.venue.is_fresh(now)
        or account.venue.positions is None
        or account.venue.external_order_products is None
        or snapshot.deployment.product_id in account.venue.external_order_products
        or unsettled_fill_evidence(snapshot)
        or any(order.kind is OrderKind.MARKETABLE for order in active_orders(snapshot))
    ):
        return False
    # Compare UTC intervals: adding the grace period to a valid fill can overflow.
    if snapshot.fills and account.venue.observed_at - max(
        f.filled_at for f in snapshot.fills
    ) <= timedelta(seconds=5):
        return False
    managed = managed_signed_base(snapshot)
    size = account.venue.contract_sizes.get(snapshot.deployment.product_id)
    if managed is None or size is None or not size.is_finite() or size <= 0:
        return False
    try:
        with localcontext() as context:
            context.traps[Inexact] = True
            count = managed / size
            return count == count.to_integral_value() and count == account.venue.positions.get(
                snapshot.deployment.product_id, Decimal(0)
            )
    except DecimalException:
        return False


async def cancel_proven_futures_orders(
    snapshot: DeploymentSnapshot,
    *,
    account: FuturesLiveAccount | None,
    broker: Broker,
    store: ExecutionStore,
    now: datetime,
) -> DeploymentSnapshot:
    """Revalidate fenced ownership before each cancel, then reconcile its racing fills."""
    for candidate in active_orders(snapshot):
        snapshot = await store.get_deployment(snapshot.deployment.id)
        order = next((item for item in active_orders(snapshot) if item.id == candidate.id), None)
        if order is None or order.venue_order_id is None:
            continue
        if not cancellation_proven(snapshot, account, now) or not _owned_order(snapshot, order):
            return snapshot
        try:
            if cancel_pending(order):
                result = await broker.get_order(
                    venue_order_id=order.venue_order_id, client_order_id=order.client_order_id
                )
                if result.status in {OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.UNKNOWN}:
                    result = replace(result, reject_reason=CANCEL_PENDING_REASON)
            else:
                result = await broker.cancel_order(
                    venue_order_id=order.venue_order_id, client_order_id=order.client_order_id
                )
        except BrokerError:
            return await store.get_deployment(snapshot.deployment.id)
        await store.save_order(
            replace(
                order,
                status=result.status,
                filled_quantity=max(order.filled_quantity, result.filled_quantity),
                updated_at=now,
                reject_reason=result.reject_reason,
            )
        )
        snapshot = await reconcile_open_orders(
            await store.get_deployment(snapshot.deployment.id), broker=broker, store=store
        )
    return snapshot
