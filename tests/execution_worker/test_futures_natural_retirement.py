"""Every live futures closure keeps durable post-fill evidence, not only explicit flatten."""

from dataclasses import replace
from decimal import Decimal

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from tests.execution.test_live_futures_entry import clock
from tests.execution.test_paper_futures_books import _PERP, _candles
from tests.execution_worker.futures_safety_support import step, stop_book
from thytrader.execution.futures_live import (
    FuturesLiveRuntime,
    live_futures_runtime_scope,
    managed_live_futures,
)
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.submit import submit_intent
from thytrader.trading.futures_safety import require_futures_flat_confirmation
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentStatus,
    IntentPurpose,
    LifecycleCommand,
    OrderKind,
    OrderSide,
)


@pytest.mark.anyio
@pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL])
@pytest.mark.parametrize(
    ("purpose", "command", "stop_after_fill"),
    [
        (IntentPurpose.STOP, LifecycleCommand.MANAGED_SHUTDOWN, False),
        (IntentPurpose.STOP, LifecycleCommand.FLATTEN, False),
        (IntentPurpose.STOP, LifecycleCommand.MANAGED_SHUTDOWN, True),
        (IntentPurpose.TAKE_PROFIT, LifecycleCommand.MANAGED_SHUTDOWN, True),
        (IntentPurpose.SIGNAL_EXIT, LifecycleCommand.NONE, False),
        (IntentPurpose.TIME_EXIT, LifecycleCommand.STOP_NEW_ENTRIES, False),
        (IntentPurpose.LIQUIDATION, LifecycleCommand.MANAGED_SHUTDOWN, False),
    ],
)
async def test_natural_closure_requires_postfill_read_after_restart(
    monkeypatch: pytest.MonkeyPatch,
    side: OrderSide,
    purpose: IntentPurpose,
    command: LifecycleCommand,
    stop_after_fill: bool,
) -> None:
    """All exit purposes, both sides, late status changes and FLATTEN share retirement proof."""
    venue = FakeVenue()
    clock(monkeypatch, venue)
    store = InMemoryExecutionStore()
    opened = await open_live_book(store, venue, side)
    assert opened.deployment.futures_flat_confirmation_after is None
    if not stop_after_fill:
        await stop_book(store, opened, command)
    else:
        # A closure while paused must not be lost when STOPPED is set afterward.
        await store.save_deployment(replace(opened.deployment, status=DeploymentStatus.PAUSED))
    kind = (
        OrderKind.STOP_LIMIT
        if purpose is IntentPurpose.STOP
        else OrderKind.POST_ONLY_LIMIT
        if purpose is IntentPurpose.TAKE_PROFIT
        else OrderKind.MARKETABLE
    )
    exit_order = await submit_intent(
        store=store,
        broker=venue.broker(),
        deployment_id=opened.deployment.id,
        product_id=_PERP,
        purpose=purpose,
        side=OrderSide.SELL if side is OrderSide.BUY else OrderSide.BUY,
        kind=kind,
        quantity=Decimal("0.02"),
        price=None if kind is OrderKind.MARKETABLE else Decimal(100),
        stop_trigger_price=Decimal(110) if kind is OrderKind.STOP_LIMIT else None,
        candle=_candles(31)[-1],
    )
    assert exit_order.venue_order_id is not None
    if kind is not OrderKind.MARKETABLE:
        venue.fill(exit_order.venue_order_id)
    if stop_after_fill:
        # Reconciliation outside the worker must commit the duty with fill economics.
        flat = await reconcile_open_orders(
            await store.get_deployment(opened.deployment.id), broker=venue.broker(), store=store
        )
        assert flat.position is None
        assert flat.deployment.futures_flat_confirmation_after is not None
        await stop_book(store, flat, command)
    with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
        flat = await step(store, venue, opened.deployment.id)
    assert flat.position is None and venue.net == 0
    assert managed_live_futures(flat)
    assert flat.deployment.futures_flat_confirmation_after is not None
    intent_count = len(flat.intents)
    for seconds in (0, 5):
        reads = venue.account_reads
        venue.advance(seconds)
        with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
            pending = await step(store, venue, opened.deployment.id)
        assert venue.account_reads > reads and managed_live_futures(pending)
    venue.advance(1)
    with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
        done = await step(store, venue, opened.deployment.id)
    assert not managed_live_futures(done)
    assert done.deployment.futures_flat_confirmation_after is None
    reads = venue.account_reads
    venue.advance()
    with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
        done = await step(store, venue, opened.deployment.id)
    assert venue.account_reads == reads, "completed historical flat books must retire reads"
    assert len(done.intents) == intent_count, "confirmation never mints order authority"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mode", "product"),
    [
        (DeploymentMode.LIVE, "BTC-USD"),
        (DeploymentMode.PAPER, "BTC-USD"),
        (DeploymentMode.PAPER, _PERP),
    ],
)
async def test_flat_projection_confirmation_does_not_change_spot_or_paper(
    mode: DeploymentMode, product: str
) -> None:
    """Even explicit flat-fill evidence leaves legacy spot/paper safety defaults unchanged."""
    venue = FakeVenue()
    opened = await open_live_book(InMemoryExecutionStore(), venue)
    deployment = replace(opened.deployment, mode=mode, product_id=product)
    unchanged = require_futures_flat_confirmation(
        deployment, after=venue.now, fill_projected_flat=True
    )
    assert unchanged == deployment
    assert unchanged.futures_flat_confirmation_after is None
    assert not unchanged.futures_derisk_pending
