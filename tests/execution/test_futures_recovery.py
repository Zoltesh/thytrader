"""FUTURE client-scan recovery and partial reductions across runtime restarts."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from thytrader.execution.futures_reduction import reduce_live_futures_position
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import IntentPurpose


@pytest.mark.anyio
@pytest.mark.parametrize("after_post", [False, True])
async def test_ambiguous_reduction_recovers_by_future_scan_never_resends(after_post: bool) -> None:
    """Fresh broker recovery preserves the original client identity across both crash windows."""
    store = InMemoryExecutionStore()
    venue = FakeVenue()
    snapshot = await open_live_book(store, venue)
    venue.timeout_after_post = after_post
    venue.timeout_before_post = not after_post
    account = await venue.read_account(frozenset())
    first = await reduce_live_futures_position(
        snapshot,
        account=account,
        broker=venue.broker(),
        store=store,
        purpose=IntentPurpose.STOP,
        now=venue.now,
    )
    creates = len(venue.creates)
    venue.advance()
    second = await reduce_live_futures_position(
        first,
        account=await venue.read_account(frozenset()),
        broker=venue.broker(),
        store=store,
        purpose=IntentPurpose.STOP,
        now=venue.now,
    )
    assert len(venue.creates) == creates
    assert (second.position is None) is after_post
    assert any(
        method == "GET"
        and path.endswith("/historical/batch")
        and isinstance(params, dict)
        and params.get("product_type") == "FUTURE"
        for method, path, params in venue.calls
    )


@pytest.mark.anyio
async def test_partial_reduction_waits_for_fresh_position_then_closes_residual() -> None:
    """Only the remaining whole contract is sent after canonical partial fill application."""
    store = InMemoryExecutionStore()
    venue = FakeVenue()
    snapshot = await open_live_book(store, venue)
    venue.partial_contracts = Decimal(1)
    account = await venue.read_account(frozenset())
    first = await reduce_live_futures_position(
        snapshot,
        account=account,
        broker=venue.broker(),
        store=store,
        purpose=IntentPurpose.STOP,
        now=venue.now,
    )
    assert first.position is not None and first.position.quantity == Decimal("0.01")
    sends = len(venue.creates)
    second = await reduce_live_futures_position(
        first,
        account=account,
        broker=venue.broker(),
        store=store,
        purpose=IntentPurpose.STOP,
        now=venue.now,
    )
    assert len(venue.creates) == sends
    venue.advance()
    venue.partial_contracts = None
    final = await reduce_live_futures_position(
        second,
        account=await venue.read_account(frozenset()),
        broker=venue.broker(),
        store=store,
        purpose=IntentPurpose.STOP,
        now=venue.now,
    )
    assert final.position is None
    assert venue.net == 0
    assert len(venue.creates) == sends + 1
