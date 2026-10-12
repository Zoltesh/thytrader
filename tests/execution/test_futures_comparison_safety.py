"""I9 evidence freshness, resting-protection interpretation and exact contract arithmetic."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from tests.execution.test_paper_futures_books import _PERP, _candles
from thytrader.execution.futures_venue_check import compare_position
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.submit import submit_intent
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import IntentPurpose, OrderKind, OrderSide, OrderStatus


@pytest.mark.anyio
async def test_resting_stop_allows_comparison_but_transactions_do_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Permanent unfilled protection cannot suppress I9 forever; uncertain protection can."""
    venue = FakeVenue()
    monkeypatch.setattr("thytrader.execution.reconcile.utc_now", lambda: venue.now)
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    order = await submit_intent(
        store=store,
        broker=venue.broker(),
        deployment_id=snapshot.deployment.id,
        product_id=_PERP,
        purpose=IntentPurpose.STOP,
        side=OrderSide.SELL,
        kind=OrderKind.STOP_LIMIT,
        quantity=Decimal("0.02"),
        price=Decimal(89),
        stop_trigger_price=Decimal(90),
        candle=_candles(31)[-1],
    )
    snapshot = await reconcile_open_orders(
        await store.get_deployment(snapshot.deployment.id), broker=venue.broker(), store=store
    )
    account = await venue.read_account(frozenset())
    assert compare_position(snapshot, account.venue, now=venue.now) is not None
    for status in (OrderStatus.PENDING, OrderStatus.UNKNOWN):
        orders = tuple(
            replace(item, status=status) if item.id == order.id else item
            for item in snapshot.orders
        )
        assert (
            compare_position(replace(snapshot, orders=orders), account.venue, now=venue.now) is None
        )
    assert (
        compare_position(replace(snapshot, accounting_complete=False), account.venue, now=venue.now)
        is None
    )
    unapplied = replace(snapshot.fills[0], economics_applied_at=None)
    assert (
        compare_position(replace(snapshot, fills=(unapplied,)), account.venue, now=venue.now)
        is None
    )
    too_early = replace(
        account.venue, observed_at=snapshot.fills[0].filled_at + timedelta(seconds=5)
    )
    assert compare_position(snapshot, too_early, now=venue.now) is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "quantity",
    ["NaN", "sNaN", "Infinity", "0", "0.01000000000000000000000000000001", "1E-999999999"],
)
async def test_invalid_or_inexact_contract_quantity_is_unknown(quantity: str) -> None:
    """Even traps-disabled rounded-to-integer and underflow cases do not certify contracts."""
    venue = FakeVenue()
    snapshot = await open_live_book(InMemoryExecutionStore(), venue)
    assert snapshot.position is not None
    snapshot = replace(snapshot, position=replace(snapshot.position, quantity=Decimal(quantity)))
    with localcontext() as context:
        context.prec = 8
        for signal in context.traps:
            context.traps[signal] = False
        assert (
            compare_position(snapshot, (await venue.read_account(frozenset())).venue, now=venue.now)
            is None
        )
