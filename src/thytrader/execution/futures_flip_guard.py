"""Reduce only excess backed by the managed client's applied fill ledger, then pause."""

from __future__ import annotations

from decimal import DecimalException, Inexact, localcontext
from typing import TYPE_CHECKING

from thytrader.execution.exit_guards import active_orders
from thytrader.execution.futures_cancel import cancel_proven_futures_orders
from thytrader.execution.futures_venue_check import compare_position
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.submit import submit_intent
from thytrader.trading.futures_repair import (
    EXCESS_REDUCTION_PREFIX,
    excess_quantity,
    managed_signed_base,
)
from thytrader.trading.models import IntentPurpose, OrderKind, OrderSide, OrderStatus

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

    from thytrader.execution.broker import Broker
    from thytrader.execution.futures_live import FuturesLiveAccount
    from thytrader.trading.models import DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore


async def reduce_proven_excess(
    snapshot: DeploymentSnapshot,
    *,
    account: FuturesLiveAccount,
    broker: Broker,
    store: ExecutionStore,
    now: datetime,
) -> DeploymentSnapshot:
    """No foreign inventory, inferred ownership, unconfirmed cancel or duplicate repair POST."""
    proof = _proof(snapshot, account, now)
    if proof is None:
        return snapshot
    if any(order.kind is OrderKind.MARKETABLE for order in active_orders(snapshot)):
        return snapshot
    snapshot = await cancel_proven_futures_orders(
        snapshot, account=account, broker=broker, store=store, now=now
    )
    snapshot = await reconcile_open_orders(snapshot, broker=broker, store=store)
    if active_orders(snapshot):
        return snapshot
    proof = _proof(snapshot, account, now)
    if proof is None:
        return snapshot
    signed, quantity = proof
    # This stable identity survives a crash before POST and after POST before the ack.
    last_fill = max(snapshot.fills, key=lambda item: (item.filled_at, str(item.id)))
    key = _attempt_key(
        snapshot, f"{EXCESS_REDUCTION_PREFIX}{snapshot.deployment.id}:{last_fill.id}"
    )
    if key is None:
        return snapshot
    await submit_intent(
        store=store,
        broker=broker,
        deployment_id=snapshot.deployment.id,
        product_id=snapshot.deployment.product_id,
        purpose=IntentPurpose.LIQUIDATION,
        side=OrderSide.SELL if signed > 0 else OrderSide.BUY,
        kind=OrderKind.MARKETABLE,
        quantity=quantity,
        price=None,
        candle=None,
        decision_at=now,
        idempotency_key=key,
    )
    return await reconcile_open_orders(
        await store.get_deployment(snapshot.deployment.id), broker=broker, store=store
    )


MAX_REDUCTION_ATTEMPTS = 3
_UNFILLED_ENDS = frozenset({OrderStatus.REJECTED, OrderStatus.CANCELED})
"""Reduction attempts per fill identity; the pause and its critical alert stay after these."""


def _attempt_key(snapshot: DeploymentSnapshot, base: str) -> str | None:
    """The next attempt's identity, or ``None`` while no attempt may be sent.

    Nothing is active here. An earlier attempt for this fill identity may be retried only if
    it ended without a fill: a definitive rejection, an unfilled cancel, or an intent that
    never reached its order. A filled attempt whose fills are not applied yet waits, and the
    attempts stop at ``MAX_REDUCTION_ATTEMPTS``. The first attempt keeps the plain identity,
    so a crash replay still finds it; later attempts append their number.
    """
    attempts = {
        intent.id
        for intent in snapshot.intents
        if intent.idempotency_key == base or (intent.idempotency_key or "").startswith(f"{base}:")
    }
    if any(
        order.status not in _UNFILLED_ENDS
        or any(fill.order_id == order.id for fill in snapshot.fills)
        for order in snapshot.orders
        if order.intent_id in attempts
    ):
        return None
    if len(attempts) >= MAX_REDUCTION_ATTEMPTS:
        return None
    return base if not attempts else f"{base}:{len(attempts)}"


def _proof(
    snapshot: DeploymentSnapshot, account: FuturesLiveAccount, now: datetime
) -> tuple[Decimal, Decimal] | None:
    """The full applied managed ledger must equal the venue; proving a subset is insufficient."""
    sample = compare_position(snapshot, account.venue, now=now)
    external = account.venue.external_order_products
    if (
        sample is None
        or sample.matches
        or external is None
        or snapshot.deployment.product_id in external
    ):
        return None
    managed = managed_signed_base(snapshot)
    size = account.venue.contract_sizes.get(snapshot.deployment.product_id)
    if managed is None or size is None:
        return None
    try:
        with localcontext() as context:
            context.traps[Inexact] = True
            if managed / size != sample.venue_contracts:
                return None
            quantity = excess_quantity(snapshot, managed)
            count = quantity / size
            if not count.is_finite() or count <= 0 or count != count.to_integral_value():
                return None
            return managed, quantity
    except DecimalException:
        return None
