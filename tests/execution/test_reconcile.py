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

    async def place_order(
        self,
        *,
        client_order_id: str,
        product_id: str,
        side: OrderSide,
        kind: OrderKind,
        quantity: Decimal,
        price: Decimal | None,
        stop_trigger_price: Decimal | None = None,
        take_profit_price: Decimal | None = None,
    ) -> SubmitResult:
        """Reconcile tests do not place orders."""
        del (
            client_order_id,
            product_id,
            side,
            kind,
            quantity,
            price,
            stop_trigger_price,
            take_profit_price,
        )
        raise AssertionError("place_order should not run in these reconcile tests")

    async def cancel_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
        """Reconcile tests do not cancel orders."""
        del venue_order_id, client_order_id
        raise AssertionError("cancel_order should not run in these reconcile tests")

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

    def maker_limit_price(
        self, *, product_id: str, mark: Decimal, side: OrderSide = OrderSide.BUY
    ) -> Decimal:
        """Reconcile tests do not size maker limits."""
        del product_id, mark, side
        raise AssertionError("maker_limit_price should not run in these reconcile tests")


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


class _FillsBroker(_LookupBroker):
    """Lookup broker that also returns configured remote fills."""

    def __init__(self, result: SubmitResult, fills: tuple[Fill, ...]) -> None:
        """Bind the get-order snapshot and the remote fill page."""
        super().__init__(result)
        self._fills = fills

    async def list_fills(self, *, product_id: str, order_id: str | None = None) -> tuple[Fill, ...]:
        """Return the configured remote fills."""
        del product_id, order_id
        return self._fills


@pytest.mark.anyio
async def test_fill_transaction_keeps_venue_reported_total_without_double_count() -> None:
    """Order status folded into the fill transaction must not inflate the venue total.

    Reconcile passes the order already carrying the venue-reported
    filled_quantity; the applied fill is the same quantity the total already
    reflects. The folded status write must keep the venue total, not add the
    fragment again.
    """
    store = InMemoryExecutionStore()
    now = utc_now()
    deployment_id = uuid7(now)
    order = Order(
        id=uuid7(now),
        deployment_id=deployment_id,
        intent_id=uuid7(now),
        client_order_id="client-venue-total",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("2"),
        status=OrderStatus.OPEN,
        created_at=now,
        updated_at=now,
        price=Decimal("100"),
        filled_quantity=Decimal("2"),
        venue_order_id="venue-1",
        product_id="BTC-USD",
    )
    deployment = Deployment(
        id=deployment_id,
        strategy_fingerprint="sha256:" + ("a" * 64),
        strategy_id=UUID(int=1),
        product_id="BTC-USD",
        mode=DeploymentMode.LIVE,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("10000"),
        phase=RuntimePhase.PENDING_ENTRY,
        pending_stop_price=Decimal("90"),
        pending_target_price=Decimal("120"),
        timeframe="1h",
        created_at=now,
        updated_at=now,
    )
    await store.create_deployment(deployment)
    await store.save_order(order)
    snapshot = await store.get_deployment(deployment_id)
    result = await reconcile_open_orders(
        snapshot,
        broker=_FillsBroker(
            SubmitResult(
                status=OrderStatus.FILLED,
                venue_order_id="venue-1",
                filled_quantity=Decimal("2"),
                fill_price=Decimal("100"),
            ),
            (
                Fill(
                    id=uuid7(now),
                    deployment_id=deployment_id,
                    order_id=order.id,
                    venue_fill_id="vf-1",
                    price=Decimal("100"),
                    quantity=Decimal("2"),
                    fee=Decimal("0.2"),
                    filled_at=now,
                    venue_order_id="venue-1",
                ),
            ),
        ),
        store=store,
        product_id="BTC-USD",
    )
    updated = next(item for item in result.orders if item.id == order.id)
    assert updated.status is OrderStatus.FILLED
    assert updated.filled_quantity == Decimal("2")
    assert result.fills
    assert result.fills[0].economics_applied_at is not None
    assert result.position is not None
    assert result.deployment.cash == Decimal("10000") - Decimal("200") - Decimal("0.2")
