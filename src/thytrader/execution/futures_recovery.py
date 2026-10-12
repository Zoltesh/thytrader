"""Recover observation anchors for live futures intents interrupted before local order commit."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.trading.ids import utc_now, uuid7
from thytrader.trading.models import DeploymentMode, Order, OrderKind, OrderStatus

if TYPE_CHECKING:
    from thytrader.trading.models import DeploymentSnapshot
    from thytrader.trading.store import ExecutionStore


async def recover_futures_intents(
    snapshot: DeploymentSnapshot, *, store: ExecutionStore
) -> DeploymentSnapshot:
    """Restore UNKNOWN local orders under the worker fence, never send or infer venue absence.

    Submission commits the intent before the local order. A crash between those writes
    leaves no order for ordinary client-id reconciliation to scan. Recover only its local
    observation anchor, retaining the durable identity and the original submission window.
    The existing reconciliation path then performs the FUTURE scan; missing evidence stays
    UNKNOWN. No successful observation time, venue identity or new submit is manufactured.
    """
    if snapshot.deployment.mode is not DeploymentMode.LIVE or not is_futures_product_id(
        snapshot.deployment.product_id
    ):
        return snapshot
    represented = {order.intent_id for order in snapshot.orders}
    for intent in snapshot.intents:
        if (
            intent.id in represented
            or intent.status not in {OrderStatus.PENDING, OrderStatus.UNKNOWN}
            or intent.kind is OrderKind.ADOPTION
            or intent.product_id != snapshot.deployment.product_id
        ):
            continue
        await store.save_order(
            Order(
                id=uuid7(utc_now()),
                deployment_id=snapshot.deployment.id,
                intent_id=intent.id,
                client_order_id=intent.client_order_id,
                product_id=intent.product_id,
                side=intent.side,
                kind=intent.kind,
                quantity=intent.quantity,
                price=intent.price,
                stop_trigger_price=intent.stop_trigger_price,
                take_profit_price=intent.take_profit_price,
                status=OrderStatus.UNKNOWN,
                created_at=intent.created_at,
                updated_at=utc_now(),
                reject_reason="Intent persisted before interrupted local order write.",
            )
        )
    return await store.get_deployment(snapshot.deployment.id)
