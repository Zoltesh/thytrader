"""Public Coinbase ticker feed supervision for the market-data worker."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
import logging
from typing import TYPE_CHECKING

from thytrader.exchanges.ws.market_feed import COINBASE_WS_URL, CoinbaseMarketFeed
from thytrader.market_data.feed_state import (
    MarketFeedSnapshot,
    MarketFeedState,
    MarketFeedStateStore,
    MarketFeedUnavailableError,
)

if TYPE_CHECKING:
    from thytrader.exchanges.ws.models import WebSocketConnectionState
    from thytrader.persistence.audit_events import AuditEventStore

_logger = logging.getLogger(__name__)
_SNAPSHOT_INTERVAL_SECONDS = 5.0


async def run_public_market_feed(
    stop_requested: asyncio.Event,
    *,
    product_id: str,
    enabled: bool,
    feed_store: MarketFeedStateStore,
    audit_store: AuditEventStore | None = None,
    ws_url: str = COINBASE_WS_URL,
    heartbeat_timeout_seconds: float = 30.0,
) -> None:
    """Run the public ticker feed, or record that it is disabled in demo mode."""
    if not enabled:
        await _record_disabled(feed_store, product_id)
        await stop_requested.wait()
        return

    persist_requested = asyncio.Event()
    persist_requested.set()

    def request_persist(_state: WebSocketConnectionState) -> None:
        persist_requested.set()

    feed = CoinbaseMarketFeed(
        product_id=product_id,
        ws_url=ws_url,
        audit_store=audit_store,
        heartbeat_timeout_seconds=heartbeat_timeout_seconds,
        on_state_changed=request_persist,
    )
    persist_task = asyncio.create_task(
        _persist_loop(stop_requested, persist_requested, feed_store, feed, product_id)
    )
    try:
        await feed.run(stop_requested)
    finally:
        persist_requested.set()
        persist_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await persist_task
        await _persist_feed(feed_store, feed, product_id)


async def _persist_loop(
    stop_requested: asyncio.Event,
    persist_requested: asyncio.Event,
    store: MarketFeedStateStore,
    feed: CoinbaseMarketFeed,
    product_id: str,
) -> None:
    """Persist feed snapshots on state change and a bounded interval."""
    while not stop_requested.is_set():
        persist_requested.clear()
        await _persist_feed(store, feed, product_id)
        waiter = asyncio.create_task(persist_requested.wait())
        stopper = asyncio.create_task(stop_requested.wait())
        _done, pending = await asyncio.wait(
            {waiter, stopper},
            timeout=_SNAPSHOT_INTERVAL_SECONDS,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()


async def _record_disabled(store: MarketFeedStateStore, product_id: str) -> None:
    """Persist an explicit disabled snapshot when no live Coinbase feed is configured."""
    snapshot = MarketFeedSnapshot(
        product_id=product_id,
        state=MarketFeedState.DISABLED,
        updated_at=datetime.now(UTC),
    )
    try:
        await store.record(snapshot)
    except MarketFeedUnavailableError:
        _logger.warning("market_feed_state_unavailable")


async def _persist_feed(
    store: MarketFeedStateStore,
    feed: CoinbaseMarketFeed,
    product_id: str,
) -> None:
    """Write the current in-memory feed facts without inventing missing prices."""
    ticker = feed.last_ticker
    snapshot = MarketFeedSnapshot(
        product_id=product_id,
        state=MarketFeedState(feed.state.value),
        last_message_at=feed.last_message_at,
        last_ticker_at=ticker.time if ticker is not None else None,
        last_price=ticker.price if ticker is not None else None,
        updated_at=datetime.now(UTC),
    )
    try:
        await store.record(snapshot)
    except MarketFeedUnavailableError:
        _logger.warning("market_feed_state_unavailable")
