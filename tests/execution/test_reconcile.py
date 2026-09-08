"""Reconcile fail-closed behavior for ambiguous and incomplete live orders."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from thytrader.execution.broker import SubmitResult
from thytrader.execution.ids import utc_now, uuid7
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentStatus,
    Fill,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    RuntimePhase,
)
from thytrader.execution.reconcile import reconcile_open_orders

if TYPE_CHECKING:
    from thytrader.market_data.models import Candle


class _LookupBroker:
    """Broker double that returns a configured get-order snapshot and no fills."""

    def __init__(self, result: SubmitResult) -> None:
        """Bind the get-order result used by reconcile."""
        self._result = result

    async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Return the configured snapshot."""
        del venue_order_id, client_order_id
        return self._result

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        """Return no remote fills."""
        del product_id, order_id
        return ()

    def match_open_order(self, order: Order, candle: Candle) -> Fill | None:
        """Live-style: no candle matching."""
        del order, candle
        return None


async def _snapshot_with_order(store: InMemoryExecutionStore, order: Order) -> Deployment:
    """Insert one running deployment and persist the given order."""
    now = utc_now()
    deployment = Deployment(
        id=order.deployment_id,
        strategy_fingerprint="sha256:" + ("a" * 64),
        strategy_id=UUID(int=1),
        product_id="BTC-USD",
        mode=DeploymentMode.LIVE,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("10000"),
        phase=RuntimePhase.PENDING_ENTRY,
        created_at=now,
        updated_at=now,
    )
    await store.create_deployment(deployment)
    await store.save_order(order)
    return deployment


@pytest.mark.anyio
async def test_reconcile_pauses_when_unknown_order_has_no_venue_id() -> None:
    """A timeout without a venue id must pause instead of being ignored."""
    store = InMemoryExecutionStore()
    now = utc_now()
    deployment_id = uuid7(now)
    order = Order(
        id=uuid7(now),
        deployment_id=deployment_id,
        intent_id=uuid7(now),
        client_order_id="client-unknown",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.01"),
        status=OrderStatus.UNKNOWN,
        created_at=now,
        updated_at=now,
        price=Decimal("100"),
        venue_order_id=None,
    )
    await _snapshot_with_order(store, order)
    snapshot = await store.get_deployment(deployment_id)
    result = await reconcile_open_orders(
        snapshot,
        broker=_LookupBroker(SubmitResult(status=OrderStatus.UNKNOWN, venue_order_id="")),
        store=store,
        product_id="BTC-USD",
    )
    assert result.deployment.status is DeploymentStatus.PAUSED
    assert result.deployment.mismatch_detail is not None


@pytest.mark.anyio
async def test_reconcile_pauses_filled_order_without_rest_fills() -> None:
    """FILLED rows stay in the watch set until local fill coverage exists."""
    store = InMemoryExecutionStore()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    deployment_id = uuid7(now)
    order = Order(
        id=uuid7(now),
        deployment_id=deployment_id,
        intent_id=uuid7(now),
        client_order_id="client-filled",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.01"),
        status=OrderStatus.FILLED,
        created_at=now,
        updated_at=now,
        price=Decimal("100"),
        filled_quantity=Decimal("0.01"),
        venue_order_id="venue-filled",
    )
    await _snapshot_with_order(store, order)
    snapshot = await store.get_deployment(deployment_id)
    result = await reconcile_open_orders(
        snapshot,
        broker=_LookupBroker(
            SubmitResult(
                status=OrderStatus.FILLED,
                venue_order_id="venue-filled",
                filled_quantity=Decimal("0.01"),
                fill_price=Decimal("100"),
            )
        ),
        store=store,
        product_id="BTC-USD",
    )
    assert result.deployment.status is DeploymentStatus.PAUSED
    assert "no REST fills" in (result.deployment.mismatch_detail or "")
