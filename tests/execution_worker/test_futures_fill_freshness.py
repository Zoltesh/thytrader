"""Canonical fill-time boundaries fail closed without escaping the fenced worker."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest

from tests.execution.decision_support import Catalog
from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from tests.execution.test_live_futures_entry import clock
from tests.execution.test_paper_futures_books import _strategy
from tests.execution_worker.futures_safety_support import protect, step, stop_book
from thytrader.execution.futures_cancel import cancellation_proven
from thytrader.execution.futures_live import FuturesLiveRuntime, live_futures_runtime_scope
from thytrader.execution.futures_venue_check import compare_position
from thytrader.execution.paper import PaperBroker
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution_worker.service import _run_cycle
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.trading.memory import InMemoryExecutionStore


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["cancel", "compare", "worker", "cycle"])
@pytest.mark.parametrize(
    ("wire_time", "age", "proven"),
    [
        pytest.param("0001-01-01T00:00:00+00:00", timedelta(seconds=6), True, id="minimum"),
        pytest.param("2030-01-01T00:00:00+00:00", timedelta(seconds=6), False, id="future"),
        pytest.param(
            "9999-12-31T23:59:54.999999+00:00",
            timedelta(seconds=6),
            False,
            id="last-addable-fill",
        ),
        pytest.param("9999-12-31T23:59:55+00:00", timedelta(seconds=6), False, id="first-overflow"),
        pytest.param("9999-12-31T23:59:59.999999+00:00", timedelta(seconds=6), False, id="maximum"),
        pytest.param(None, timedelta(seconds=5, microseconds=-1), False, id="below-five"),
        pytest.param(None, timedelta(seconds=5), False, id="exact-five"),
        pytest.param(None, timedelta(seconds=5, microseconds=1), True, id="above-five"),
    ],
)
async def test_wire_fill_freshness_preserves_proofs_and_worker_duties(
    monkeypatch: pytest.MonkeyPatch,
    path: Literal["cancel", "compare", "worker", "cycle"],
    wire_time: str | None,
    age: timedelta,
    proven: bool,
) -> None:
    """Retain raw parsed extremes and economics; only a strict post-fill proof retires duty."""
    venue = FakeVenue()
    clock(monkeypatch, venue)
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    snapshot, order = await protect(store, venue, snapshot)
    assert order.venue_order_id is not None
    venue.fill(order.venue_order_id)
    timestamp = wire_time or venue.now.isoformat()
    venue.fills[-1]["trade_time"] = timestamp
    venue.fills[-1]["sequence_timestamp"] = timestamp
    snapshot = await reconcile_open_orders(snapshot, broker=venue.broker(), store=store)
    instant = datetime.fromisoformat(timestamp)
    assert instant.utcoffset() == timedelta(0)
    assert any(fill.filled_at == instant for fill in snapshot.fills)
    assert snapshot.position is None and venue.net == 0
    assert all(fill.economics_applied_at is not None for fill in snapshot.fills)
    duty = snapshot.deployment.futures_flat_confirmation_after
    assert duty is not None
    await stop_book(store, snapshot)
    venue.now += age
    post_count = sum(method == "POST" for method, _, _ in venue.calls)

    # Each pass has a new runtime and broker; retained canonical evidence must stay safe.
    for _ in range(2):
        account = await venue.read_account(frozenset())
        if path == "cancel":
            assert cancellation_proven(snapshot, account, venue.now) is proven
        elif path == "compare":
            comparison = compare_position(snapshot, account.venue, now=venue.now)
            assert (comparison is not None) is proven
            if comparison is not None:
                assert comparison.matches and comparison.book_contracts == 0
        else:
            with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
                if path == "worker":
                    await step(store, venue, snapshot.deployment.id)
                else:
                    await _run_cycle(
                        store=store,
                        publication_store=Catalog(_strategy()),
                        market_data=MarketDataService(DemoMarketData()),
                        paper_broker=PaperBroker(),
                        live_broker=None,
                        futures_live_broker=venue.broker(),
                        quote_reader=None,
                        risk_store=None,
                    )
            current = await store.get_deployment(snapshot.deployment.id)
            assert current.deployment.futures_flat_confirmation_after == (None if proven else duty)
            assert current.fills == snapshot.fills
            assert current.deployment.cash == snapshot.deployment.cash
            assert current.position is None
    assert sum(method == "POST" for method, _, _ in venue.calls) == post_count
    assert instant.tzinfo == UTC


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["cancel", "compare"])
@pytest.mark.parametrize(
    ("filled_at", "observed_at", "proven"),
    [
        ("0001-01-01T00:00:00+00:00", "0001-01-01T00:00:00+00:00", False),
        ("0001-01-01T00:00:00+00:00", "0001-01-01T00:00:04.999999+00:00", False),
        ("0001-01-01T00:00:00+00:00", "0001-01-01T00:00:05+00:00", False),
        ("0001-01-01T00:00:00+00:00", "0001-01-01T00:00:05.000001+00:00", True),
        ("9999-12-31T23:59:54.999998+00:00", "9999-12-31T23:59:59.999999+00:00", True),
        ("9999-12-31T23:59:54.999999+00:00", "9999-12-31T23:59:59.999999+00:00", False),
        ("9999-12-31T23:59:55+00:00", "9999-12-31T23:59:59.999999+00:00", False),
    ],
)
async def test_direct_freshness_intervals_at_both_datetime_bounds(
    monkeypatch: pytest.MonkeyPatch,
    path: Literal["cancel", "compare"],
    filled_at: str,
    observed_at: str,
    proven: bool,
) -> None:
    """Neither shifting the observation backward nor clamping the fill may alter the rule."""
    venue = FakeVenue()
    clock(monkeypatch, venue)
    snapshot = await open_live_book(InMemoryExecutionStore(), venue)
    snapshot = replace(
        snapshot,
        fills=tuple(
            replace(fill, filled_at=datetime.fromisoformat(filled_at)) for fill in snapshot.fills
        ),
    )
    now = datetime.fromisoformat(observed_at)
    account = await venue.read_account(frozenset())
    account = replace(account, venue=replace(account.venue, observed_at=now))
    if path == "cancel":
        assert cancellation_proven(snapshot, account, now) is proven
    else:
        comparison = compare_position(snapshot, account.venue, now=now)
        assert (comparison is not None) is proven
        if comparison is not None:
            assert comparison.matches
