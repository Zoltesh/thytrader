"""Paper discretionary books advance bar by bar like strategy books: stop first, no look-back."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from tests.execution.test_discretionary import _product, _request
from thytrader.execution import discretionary
from thytrader.execution.discretionary import place_discretionary_order, process_discretionary_bar
from thytrader.execution.discretionary_book import desk_committed_for_gate
from thytrader.execution.mark_context import closed_mark_context
from thytrader.execution.paper import PaperBroker
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.models import Candle, MarketProduct
from thytrader.market_data.service import MarketDataService
from thytrader.risk.accounting_evidence import bound_risk_market_data
from thytrader.trading.geometry import entry_bar_bucket
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    DeploymentSnapshot,
    IntentPurpose,
    OrderSide,
    OrderStatus,
    RuntimePhase,
)

if TYPE_CHECKING:
    from thytrader.execution.discretionary_request import DiscretionaryOrderRequest

pytestmark = pytest.mark.anyio
# Bars after the placement bar, which is the newest bar closed when the order was placed.
_START = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def _bar(start: datetime, *, low: Decimal, high: Decimal, close: Decimal) -> Candle:
    return Candle(starts_at=start, open=close, high=high, low=low, close=close, volume=Decimal(10))


async def _filled_long(store: InMemoryExecutionStore) -> DeploymentSnapshot:
    """A paper long whose take-profit rests after one quiet bar."""
    snapshot = await place_discretionary_order(
        store=store,
        broker=PaperBroker(),
        market_data=MarketDataService(DemoMarketData()),
        request=_request(),
        live_allowed=False,
    )
    position = snapshot.position
    assert position is not None
    quiet = _bar(
        _START, low=position.entry_price, high=position.entry_price, close=position.entry_price
    )
    return await process_discretionary_bar(
        snapshot, product=_product(), candles=(quiet,), broker=PaperBroker(), store=store
    )


async def test_a_take_profit_yields_to_a_stop_the_same_bar_traded_through() -> None:
    """The bar cannot show which traded first, so the stop exit wins (ADR 0083's rule)."""
    store = InMemoryExecutionStore()
    rested = await _filled_long(store)
    position = rested.position
    assert position is not None
    assert position.target_price is not None
    assert any(intent.purpose is IntentPurpose.TAKE_PROFIT for intent in rested.intents), (
        rested.orders
    )
    both = _bar(
        _START + timedelta(minutes=5),
        low=position.stop_price - 1,
        high=position.target_price + 1,
        close=position.entry_price,
    )
    after = await process_discretionary_bar(
        rested, product=_product(), candles=(both,), broker=PaperBroker(), store=store
    )
    assert after.position is None
    sides = {order.id: order.side for order in after.orders}
    exits = [fill.price for fill in after.fills if sides[fill.order_id] is OrderSide.SELL]
    assert exits
    assert max(exits) < position.entry_price


async def test_a_fresh_limit_never_fills_against_the_bar_closed_before_it_was_placed() -> None:
    """Placement sets the evaluation cursor to the mark bar, so matching starts after it."""
    store = InMemoryExecutionStore()
    market_data = MarketDataService(DemoMarketData())
    _, mark = await closed_mark_context(market_data, product_id="BTC-USD", timeframe="5m")
    limit = (mark.close * Decimal("0.999")).quantize(Decimal("0.01"))
    snapshot = await place_discretionary_order(
        store=store,
        broker=PaperBroker(),
        market_data=market_data,
        request=_request(entry_kind="post_only_limit", limit_price=str(limit)),
        live_allowed=False,
    )
    assert snapshot.deployment.phase is RuntimePhase.PENDING_ENTRY
    cursor = snapshot.deployment.last_evaluated_bar
    assert cursor is not None
    earlier = _bar(cursor, low=Decimal(1), high=Decimal(1000000), close=Decimal(50000))
    after = await process_discretionary_bar(
        snapshot, product=_product(), candles=(earlier,), broker=PaperBroker(), store=store
    )
    assert after.fills == ()
    assert any(order.status is OrderStatus.OPEN for order in after.orders)


async def test_a_discretionary_bar_refreshes_the_high_water_mark() -> None:
    """The drawdown breaker measures from a high-water mark the bars keep current."""
    store = InMemoryExecutionStore()
    rested = await _filled_long(store)
    position = rested.position
    assert position is not None
    higher = position.entry_price * Decimal("1.1")
    after = await process_discretionary_bar(
        rested,
        product=_product(),
        candles=(_bar(_START + timedelta(minutes=5), low=higher, high=higher, close=higher),),
        broker=PaperBroker(),
        store=store,
    )
    high_water = after.deployment.high_water_mark_equity
    assert high_water is not None
    assert high_water > Decimal(10000)


async def test_placement_binds_the_market_data_its_risk_evidence_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Midnight marks for peer books are read through the request's market data."""
    seen: list[object] = []
    original = discretionary._mark_context

    async def spy(
        market_data: MarketDataService, request: DiscretionaryOrderRequest
    ) -> tuple[MarketProduct, Candle]:
        seen.append(bound_risk_market_data())
        return await original(market_data, request)

    monkeypatch.setattr(discretionary, "_mark_context", spy)
    market_data = MarketDataService(DemoMarketData())
    await place_discretionary_order(
        store=InMemoryExecutionStore(),
        broker=PaperBroker(),
        market_data=market_data,
        request=_request(),
        live_allowed=False,
    )
    assert seen == [market_data]


def test_the_desk_gate_sees_the_whole_allocation_whatever_the_rounding() -> None:
    """Committed plus the sized notional equals the other books plus this allocation."""
    assert desk_committed_for_gate(
        committed=Decimal(100), allocation=Decimal("37.50"), notional=Decimal("36.70")
    ) + Decimal("36.70") == Decimal("137.50")
    assert desk_committed_for_gate(
        committed=Decimal(100), allocation=None, notional=Decimal("36.70")
    ) == Decimal(100)


async def test_admission_past_a_bar_close_starts_after_the_newest_closed_bar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mark bar may be older than the placement: the cursor is the last bar closed by then."""
    market_data = MarketDataService(DemoMarketData())
    _, mark = await closed_mark_context(market_data, product_id="BTC-USD", timeframe="5m")
    later = mark.starts_at + timedelta(minutes=15, seconds=1)
    monkeypatch.setattr(discretionary, "utc_now", lambda: later)
    limit = (mark.close * Decimal("0.999")).quantize(Decimal("0.01"))
    snapshot = await place_discretionary_order(
        store=InMemoryExecutionStore(),
        broker=PaperBroker(),
        market_data=market_data,
        request=_request(entry_kind="post_only_limit", limit_price=str(limit)),
        live_allowed=False,
    )
    assert snapshot.deployment.last_evaluated_bar == mark.starts_at + timedelta(minutes=10)


async def _resting_limit(store: InMemoryExecutionStore) -> DeploymentSnapshot:
    """A fresh paper limit resting just under the market."""
    market_data = MarketDataService(DemoMarketData())
    _, mark = await closed_mark_context(market_data, product_id="BTC-USD", timeframe="5m")
    limit = (mark.close * Decimal("0.999")).quantize(Decimal("0.01"))
    return await place_discretionary_order(
        store=store,
        broker=PaperBroker(),
        market_data=market_data,
        request=_request(entry_kind="post_only_limit", limit_price=str(limit)),
        live_allowed=False,
    )


async def test_a_lagging_bar_never_matches_or_moves_the_cursor_back() -> None:
    """A bar older than the cursor (the feed lags the placement) is maintenance only."""
    store = InMemoryExecutionStore()
    snapshot = await _resting_limit(store)
    cursor = snapshot.deployment.last_evaluated_bar
    assert cursor is not None
    older = _bar(
        cursor - timedelta(minutes=5), low=Decimal(1), high=Decimal(1000000), close=Decimal(50000)
    )
    after = await process_discretionary_bar(
        snapshot, product=_product(), candles=(older,), broker=PaperBroker(), store=store
    )
    assert after.fills == ()
    assert after.deployment.last_evaluated_bar == cursor


async def test_an_order_placed_after_a_bar_closed_never_fills_against_it() -> None:
    """Even behind the cursor's back, a bar that closed before the order existed is not used."""
    store = InMemoryExecutionStore()
    snapshot = await _resting_limit(store)
    entry = snapshot.orders[0]
    bar_start = entry_bar_bucket(entry.created_at, "5m") - timedelta(minutes=5)
    rewound = replace(snapshot.deployment, last_evaluated_bar=bar_start - timedelta(minutes=5))
    await store.save_deployment(rewound)
    closed_before = _bar(bar_start, low=Decimal(1), high=Decimal(1000000), close=Decimal(50000))
    after = await process_discretionary_bar(
        await store.get_deployment(rewound.id),
        product=_product(),
        candles=(closed_before,),
        broker=PaperBroker(),
        store=store,
    )
    assert after.fills == ()
