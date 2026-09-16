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
    with_runtime,
)
from thytrader.execution.reconcile import reconcile_open_orders

if TYPE_CHECKING:
    from thytrader.market_data.models import Candle


class _LookupBroker:
    """Broker double that returns a configured get-order snapshot and configured fills."""

    def __init__(self, result: SubmitResult, *, fills: tuple[Fill, ...] = ()) -> None:
        """Bind the get-order result and remote fills used by reconcile."""
        self._result = result
        self._fills = fills

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
        """Return the configured remote fills."""
        del product_id, order_id
        return self._fills

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


@pytest.mark.anyio
async def test_reconcile_resolves_an_unknown_order_using_only_its_persisted_venue_id() -> None:
    """F36: a persisted venue id from an ambiguous POST reconciles without a second create.

    Models the post-F36-fix shape: ``place_order`` already returned ``UNKNOWN``
    with the known venue id from a successful POST whose follow-up GET failed.
    Reconciliation must resolve state using only that persisted venue id
    (``_LookupBroker.place_order`` raises if it is ever called) and must apply
    the real fill it finds, with real quantity and fee, exactly once.
    """
    store = InMemoryExecutionStore()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    deployment_id = uuid7(now)
    client_order_id = "client-ambiguous"
    order = Order(
        id=uuid7(now),
        deployment_id=deployment_id,
        intent_id=uuid7(now),
        client_order_id=client_order_id,
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.01"),
        status=OrderStatus.UNKNOWN,
        created_at=now,
        updated_at=now,
        price=Decimal("100"),
        # Persisted independently of the failed GET (F36): the venue id survived.
        venue_order_id="venue-ambiguous",
    )
    deployment = await _snapshot_with_order(store, order)
    pending = with_runtime(
        deployment,
        updated_at=now,
        pending_stop_price=Decimal("50"),
        pending_target_price=Decimal("200"),
    )
    await store.save_deployment(pending)
    snapshot = await store.get_deployment(deployment_id)
    remote_fill = Fill(
        id=uuid7(now),
        deployment_id=deployment_id,
        order_id=order.id,
        venue_fill_id="fill-ambiguous-1",
        price=Decimal("100"),
        quantity=Decimal("0.01"),
        fee=Decimal("0.05"),
        filled_at=now,
    )
    broker = _LookupBroker(
        SubmitResult(
            status=OrderStatus.FILLED,
            venue_order_id="venue-ambiguous",
            filled_quantity=Decimal("0.01"),
            fill_price=Decimal("100"),
        ),
        fills=(remote_fill,),
    )
    result = await reconcile_open_orders(
        snapshot,
        broker=broker,
        store=store,
        product_id="BTC-USD",
    )
    # place_order would have raised AssertionError had reconcile minted a new create
    # request; reaching here at all proves it never did.
    assert result.deployment.status is DeploymentStatus.RUNNING
    assert result.position is not None
    assert result.position.quantity == Decimal("0.01")
    expected_cash = Decimal("10000") - (Decimal("100") * Decimal("0.01")) - Decimal("0.05")
    assert result.deployment.cash == expected_cash
    assert len(result.fills) == 1
    assert result.fills[0].fee == Decimal("0.05")


@pytest.mark.anyio
async def test_reconcile_applies_an_unapplied_local_fill_instead_of_skipping() -> None:
    """F01: a FILLED order whose local fill is not yet applied must not look covered.

    The pre-fix ``_needs_reconcile`` summed fill rows regardless of ``applied_at``,
    so a crash after ``save_fill`` made the next reconcile skip the order and
    never repair cash/inventory. Recovery must apply the recorded fill exactly
    once even when the broker reports no additional REST fills.
    """
    store = InMemoryExecutionStore()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    deployment_id = uuid7(now)
    order = Order(
        id=uuid7(now),
        deployment_id=deployment_id,
        intent_id=uuid7(now),
        client_order_id="client-unapplied",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.01"),
        status=OrderStatus.FILLED,
        created_at=now,
        updated_at=now,
        price=Decimal("100000"),
        filled_quantity=Decimal("0.01"),
        venue_order_id="venue-unapplied",
    )
    deployment = await _snapshot_with_order(store, order)
    pending = with_runtime(
        deployment,
        updated_at=now,
        pending_stop_price=Decimal("50000"),
        pending_target_price=Decimal("200000"),
    )
    await store.save_deployment(pending)
    fill = Fill(
        id=uuid7(now),
        deployment_id=deployment_id,
        order_id=order.id,
        venue_fill_id="venue-unapplied:immediate",
        price=Decimal("100000"),
        quantity=Decimal("0.01"),
        fee=Decimal("1"),
        filled_at=now,
    )
    await store.save_fill(fill)
    snapshot = await store.get_deployment(deployment_id)
    broker = _LookupBroker(
        SubmitResult(
            status=OrderStatus.FILLED,
            venue_order_id="venue-unapplied",
            filled_quantity=Decimal("0.01"),
            fill_price=Decimal("100000"),
        )
    )
    result = await reconcile_open_orders(
        snapshot,
        broker=broker,
        store=store,
        product_id="BTC-USD",
    )
    assert result.deployment.status is DeploymentStatus.RUNNING
    assert result.position is not None
    assert result.position.quantity == Decimal("0.01")
    assert result.deployment.cash == Decimal("8999")
    assert result.fills[0].applied_at is not None
