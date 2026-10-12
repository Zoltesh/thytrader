"""The synthetic futures clock owns funding time without disabling freshness gates."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal
import sys

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_live_futures_entry import clock
from tests.execution.test_paper_futures_books import (
    _PERP,
    _binding,
    _book,
    _Observations,
    _record,
    _strategy,
)
from tests.execution_worker import test_live_futures_step as worker_cases
from thytrader.execution import futures_paper
from thytrader.execution.entry import _futures_entry_block
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.trading.futures_book import InMemoryFuturesContractStore, futures_book_scope
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentMode

_HOST_TIMES = (
    datetime(2026, 10, 12, 5, 59, 59, tzinfo=UTC),
    datetime(2026, 10, 12, 6, 0, 1, tzinfo=UTC),
)


def _host_clock(monkeypatch: pytest.MonkeyPatch, instant: datetime) -> None:
    """Control only the formerly unpinned consumer, never the builtin datetime type."""

    class HostClock(datetime):
        """Stand in for a host on either side of the original funding cutoff."""

        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:
            """Preserve datetime.now timezone semantics for the controlled host."""
            return instant.astimezone(tz) if tz is not None else instant.replace(tzinfo=None)

    monkeypatch.setattr(futures_paper, "datetime", HostClock)


@pytest.mark.anyio
@pytest.mark.parametrize("host_time", _HOST_TIMES)
async def test_worker_fixture_ignores_host_funding_cutoff(
    monkeypatch: pytest.MonkeyPatch, host_time: datetime
) -> None:
    """Identical real worker inputs and order assertions pass before and after the cutoff."""
    _host_clock(monkeypatch, host_time)
    await worker_cases.test_running_worker_short_never_reads_spot_base(monkeypatch)


@pytest.mark.anyio
@pytest.mark.parametrize("host_time", _HOST_TIMES)
@pytest.mark.parametrize(
    "age",
    [None, timedelta(hours=1), timedelta(days=1), timedelta(days=1, microseconds=1)],
    ids=["missing", "fresh", "inclusive-day-boundary", "stale"],
)
async def test_fixture_funding_freshness_uses_venue_time(
    monkeypatch: pytest.MonkeyPatch, host_time: datetime, age: timedelta | None
) -> None:
    """Real preparation and entry admission retain missing/stale denial under both hosts."""
    _host_clock(monkeypatch, host_time)
    venue = FakeVenue()
    clock(monkeypatch, venue)
    store = InMemoryExecutionStore()
    strategy = _strategy()
    snapshot = await _book(store, strategy)
    snapshot = replace(snapshot, deployment=replace(snapshot.deployment, mode=DeploymentMode.LIVE))
    contracts = InMemoryFuturesContractStore()
    await contracts.bind_contract(_binding(snapshot))
    records = () if age is None else (_record(venue.now - age, "0.001"),)
    with futures_paper.futures_runtime_scope(
        futures_paper.FuturesRuntime(contracts, _Observations(records))
    ):
        state, prepared = await futures_paper.prepare_futures_book(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=MarketDataService(DemoMarketData()),
        )
        assert state is not None
        with futures_book_scope(state):
            denied = _futures_entry_block(prepared, product_id=_PERP)
        fresh = age is not None and age <= timedelta(days=1)
        if fresh:
            assert state.latest_funding_rate == Decimal("0.001")
            assert denied is None
        else:
            assert state.latest_funding_rate is None
            assert denied is not None and denied[0] == "FUNDING_HISTORY_MISSING"
        # Move the fixture, not the host or funding record: yesterday's rate must age out.
        venue.now += timedelta(days=2)
        expired, prepared = await futures_paper.prepare_futures_book(
            snapshot,
            strategy=strategy,
            store=store,
            market_data=MarketDataService(DemoMarketData()),
        )
        assert expired is not None and expired.latest_funding_rate is None
        with futures_book_scope(expired):
            denied = _futures_entry_block(prepared, product_id=_PERP)
        assert denied is not None and denied[0] == "FUNDING_HISTORY_MISSING"


def test_fixture_clock_advances_and_restores_every_patch() -> None:
    """Nested fixture lifetimes restore host clocks and imported utc_now identities exactly."""
    original_datetime = futures_paper.datetime
    originals = tuple(
        (module, module.utc_now)
        for name, module in tuple(sys.modules.items())
        if name.startswith("thytrader.") and hasattr(module, "utc_now")
    )
    for host_time in _HOST_TIMES:
        with pytest.MonkeyPatch.context() as host_patch:
            _host_clock(host_patch, host_time)
            host_datetime = futures_paper.datetime
            venue = FakeVenue()
            with pytest.MonkeyPatch.context() as fixture_patch:
                clock(fixture_patch, venue)
                assert futures_paper.datetime.now(UTC) == venue.now
                venue.advance()
                assert futures_paper.datetime.now(UTC) == venue.now
                assert all(module.utc_now() == venue.now for module, _ in originals)
            assert futures_paper.datetime is host_datetime
            assert futures_paper.datetime.now(UTC) == host_time
            assert all(module.utc_now is original for module, original in originals)
        assert futures_paper.datetime is original_datetime
