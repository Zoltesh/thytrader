"""In-memory watchlist behavior."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from thytrader.market_data.models import CandleInterval
from thytrader.market_data.watchlist import (
    InMemoryMarketDataWatchlistStore,
    MarketDataWatchTarget,
    ensure_default_watch_target,
)


def test_watchlist_upsert_and_enabled_listing() -> None:
    """Enabled targets are listed; disabled targets remain in list_all."""

    async def exercise() -> None:
        store = InMemoryMarketDataWatchlistStore()
        now = datetime(2026, 9, 10, tzinfo=UTC)
        eth = await store.upsert(
            MarketDataWatchTarget(
                provider="demo",
                product_id="ETH-USD",
                timeframe=CandleInterval.FIVE_MINUTES,
                lookback_hours=168,
                enabled=True,
                updated_at=now,
            )
        )
        await store.upsert(
            MarketDataWatchTarget(
                provider="demo",
                product_id="BTC-USD",
                timeframe=CandleInterval.ONE_HOUR,
                lookback_hours=168,
                enabled=False,
                updated_at=now,
            )
        )
        enabled = await store.list_enabled()
        listed = await store.list_all()
        fetched = await store.get("demo", "ETH-USD", CandleInterval.FIVE_MINUTES)
        assert eth.created_at == now
        assert fetched == eth
        assert tuple(item.product_id for item in enabled) == ("ETH-USD",)
        assert {item.product_id for item in listed} == {"BTC-USD", "ETH-USD"}

    asyncio.run(exercise())


def test_ensure_default_watch_target_is_idempotent() -> None:
    """An empty watchlist is seeded once with the configured 1h product."""

    async def exercise() -> None:
        store = InMemoryMarketDataWatchlistStore()
        now = datetime(2026, 9, 10, tzinfo=UTC)
        await ensure_default_watch_target(
            store,
            provider="demo",
            product_id="BTC-USD",
            lookback_hours=168,
            now=now,
        )
        await ensure_default_watch_target(
            store,
            provider="demo",
            product_id="ETH-USD",
            lookback_hours=24,
            now=now,
        )
        listed = await store.list_all()
        assert len(listed) == 1
        assert listed[0].product_id == "BTC-USD"
        assert listed[0].timeframe is CandleInterval.ONE_HOUR

    asyncio.run(exercise())
