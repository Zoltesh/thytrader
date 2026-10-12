"""Recover the intent-only crash window before a local order or venue POST exists."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from tests.execution.test_live_futures_entry import clock
from tests.execution.test_paper_futures_books import _PERP, _book, _candles, _strategy
from thytrader.execution.futures_live import FuturesLiveRuntime, live_futures_runtime_scope
from thytrader.execution.submit import submit_intent
from thytrader.execution_worker.futures_step import supervised_live_futures
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentStatus,
    IntentPurpose,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
)


class InterruptedOrderStore(InMemoryExecutionStore):
    """Interrupt the first local reduction order write, after the durable intent commit."""

    interrupt = False

    async def save_order(self, order: Order) -> Order:
        """Model process loss rather than an adapter timeout after the order already exists."""
        if self.interrupt:
            self.interrupt = False
            raise KeyboardInterrupt("synthetic intent-only crash")
        return await super().save_order(order)


@pytest.mark.anyio
@pytest.mark.parametrize("stopped_flat", [False, True])
async def test_restart_scans_intent_without_local_order_and_never_resubmits(
    monkeypatch: pytest.MonkeyPatch,
    stopped_flat: bool,
) -> None:
    """A restart restores the local observation anchor, not a new submission authority."""
    venue = FakeVenue()
    clock(monkeypatch, venue)
    store = InterruptedOrderStore()
    snapshot = (
        await _book(store, _strategy()) if stopped_flat else await open_live_book(store, venue)
    )
    if stopped_flat:
        await store.save_deployment(replace(snapshot.deployment, mode=DeploymentMode.LIVE))
    store.interrupt = True
    before = len(venue.creates)
    with pytest.raises(KeyboardInterrupt, match="intent-only"):
        await submit_intent(
            store=store,
            broker=venue.broker(),
            deployment_id=snapshot.deployment.id,
            product_id=_PERP,
            purpose=IntentPurpose.ENTRY if stopped_flat else IntentPurpose.STOP,
            side=OrderSide.BUY if stopped_flat else OrderSide.SELL,
            kind=OrderKind.POST_ONLY_LIMIT if stopped_flat else OrderKind.MARKETABLE,
            quantity=Decimal("0.02"),
            price=venue.price if stopped_flat else None,
            candle=_candles(31)[-1] if stopped_flat else None,
            decision_at=venue.now,
            idempotency_key="test-intent-only-reduction",
        )
    if stopped_flat:
        current = await store.get_deployment(snapshot.deployment.id)
        await store.save_deployment(replace(current.deployment, status=DeploymentStatus.STOPPED))
    crashed = await store.get_deployment(snapshot.deployment.id)
    intent = crashed.intents[-1]
    assert all(order.intent_id != intent.id for order in crashed.orders)
    assert len(venue.creates) == before
    # Recreate runtime and broker as a restarted process would.
    for _ in range(2):
        venue.advance()
        with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
            async with supervised_live_futures(
                await store.get_deployment(snapshot.deployment.id),
                state=None,
                broker=venue.broker(),
                store=store,
                policy=compiled_default_risk_policy(),
                portfolio=(crashed,),
            ):
                pass
    recovered = await store.get_deployment(snapshot.deployment.id)
    orders = [order for order in recovered.orders if order.intent_id == intent.id]
    assert len(orders) == 1
    assert orders[0].client_order_id == intent.client_order_id
    assert orders[0].status is OrderStatus.UNKNOWN
    assert orders[0].venue_observed_at is None
    assert len(venue.creates) == before
    assert any(
        method == "GET"
        and path.endswith("/historical/batch")
        and isinstance(params, dict)
        and params.get("product_type") == "FUTURE"
        for method, path, params in venue.calls
    )
