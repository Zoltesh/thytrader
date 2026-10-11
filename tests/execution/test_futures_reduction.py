"""Price-free futures exits exercise actual intent, adapter, reconcile and fill transactions."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_paper_futures_books import _PERP, _book, _candles, _product, _strategy
from thytrader.execution.exits import _marketable_exit
from thytrader.execution.futures_live import live_futures_account_scope
from thytrader.execution.futures_reduction import reduce_live_futures_position
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.submit import submit_intent
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentSnapshot,
    IntentPurpose,
    OrderKind,
    OrderSide,
)


async def open_live_book(
    store: InMemoryExecutionStore, venue: FakeVenue, side: OrderSide = OrderSide.BUY
) -> DeploymentSnapshot:
    """Open through real persisted intent plus canonical synthetic venue fills."""
    snapshot = await _book(store, _strategy())
    await store.save_deployment(
        replace(
            snapshot.deployment,
            mode=DeploymentMode.LIVE,
            cash=Decimal(0),
            initial_equity=Decimal(0),
            allocated_capital=Decimal(100),
            pending_stop_price=Decimal(90) if side is OrderSide.BUY else Decimal(150),
        )
    )
    order = await submit_intent(
        store=store,
        broker=venue.broker(),
        deployment_id=snapshot.deployment.id,
        product_id=_PERP,
        purpose=IntentPurpose.ENTRY,
        side=side,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.02"),
        price=venue.price,
        candle=_candles(31)[-1],
    )
    venue.fill(order.venue_order_id or "")
    venue.advance()
    current = await store.get_deployment(snapshot.deployment.id)
    return await reconcile_open_orders(
        current, broker=venue.broker(), store=store, product_id=_PERP
    )


@pytest.mark.anyio
@pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL])
async def test_candleless_reduction_closes_long_and_short_with_real_fees(side: OrderSide) -> None:
    """No candle/entry/funding gate or mark price is required to close known inventory."""
    store = InMemoryExecutionStore()
    venue = FakeVenue()
    snapshot = await open_live_book(store, venue, side)
    assert snapshot.position is not None
    assert snapshot.position.quantity == Decimal("0.02")
    account = await venue.read_account(frozenset())
    account = replace(account, marks={})
    closed = await reduce_live_futures_position(
        snapshot,
        account=account,
        broker=venue.broker(),
        store=store,
        purpose=IntentPurpose.STOP,
        now=venue.now,
    )
    assert closed.position is None
    assert venue.net == 0
    assert closed.deployment.cash == Decimal("-0.30")
    assert len(closed.fills) == 2
    assert all(fill.economics_applied_at is not None for fill in closed.fills)
    assert closed.intents[-1].price is None


@pytest.mark.anyio
@pytest.mark.parametrize("purpose", [IntentPurpose.SIGNAL_EXIT, IntentPurpose.TIME_EXIT])
async def test_existing_exit_path_uses_explicit_position_reduction(
    purpose: IntentPurpose, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Signal/time exits share the candle-free quantity/ownership guard, never a priced order."""
    store = InMemoryExecutionStore()
    venue = FakeVenue()
    snapshot = await open_live_book(store, venue)
    monkeypatch.setattr("thytrader.execution.exits.utc_now", lambda: venue.now)
    with live_futures_account_scope(await venue.read_account(frozenset())):
        closed = await _marketable_exit(
            snapshot,
            candle=_candles(32)[-1],
            product=_product(),
            broker=venue.broker(),
            store=store,
            purpose=purpose,
            price=Decimal(131),
        )
    assert closed.position is None
    assert closed.intents[-1].price is None
