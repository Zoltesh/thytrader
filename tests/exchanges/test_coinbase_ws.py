"""Behavioral tests for Coinbase WebSocket market ticker feed and lifecycle management."""

import asyncio
from decimal import Decimal
import json

import pytest
from websockets.asyncio.server import ServerConnection, serve

from thytrader.exchanges.ws.market_feed import CoinbaseMarketFeed
from thytrader.exchanges.ws.models import (
    TickerMessage,
    WebSocketConnectionState,
)
from thytrader.persistence.audit_events import (
    AuditEventCategory,
    InMemoryAuditEventStore,
)


@pytest.mark.anyio
async def test_websocket_feed_connects_receives_ticker_and_records_audit_events() -> None:
    """Feed connects to test WS server, parses ticker data, and records audit trail."""
    received_tickers: list[TickerMessage] = []

    async def fake_handler(websocket: ServerConnection) -> None:
        # Wait for subscriptions
        await websocket.recv()
        await websocket.recv()

        ticker_payload = {
            "channel": "ticker",
            "events": [
                {
                    "type": "snapshot",
                    "tickers": [
                        {
                            "type": "ticker",
                            "product_id": "BTC-USD",
                            "price": "65000.50",
                            "volume_24_h": "12000.25",
                            "low_24_h": "64000.00",
                            "high_24_h": "66000.00",
                            "low_52_w": "30000.00",
                            "high_52_w": "70000.00",
                            "price_percent_chg_24_h": "1.55",
                        }
                    ],
                }
            ],
        }
        await websocket.send(json.dumps(ticker_payload))
        # Keep connection open briefly
        await asyncio.sleep(0.5)

    audit_store = InMemoryAuditEventStore()

    async with serve(fake_handler, "127.0.0.1", 0) as server:
        sockets = list(server.sockets)
        port = sockets[0].getsockname()[1]
        ws_url = f"ws://127.0.0.1:{port}"

        feed = CoinbaseMarketFeed(
            product_id="BTC-USD",
            ws_url=ws_url,
            audit_store=audit_store,
            on_ticker=received_tickers.append,
            heartbeat_timeout_seconds=2.0,
        )

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(stop))

        # Wait for ticker to arrive
        for _ in range(20):
            if len(received_tickers) > 0:
                break
            await asyncio.sleep(0.05)

        assert len(received_tickers) == 1
        t = received_tickers[0]
        assert t.product_id == "BTC-USD"
        assert t.price == Decimal("65000.50")
        assert feed.state == WebSocketConnectionState.CONNECTED
        assert feed.last_ticker is not None

        stop.set()
        await task

    events = await audit_store.list_recent(limit=20)
    assert len(events) >= 2
    categories = [e.category for e in events]
    assert all(c == AuditEventCategory.WEBSOCKET for c in categories)


@pytest.mark.anyio
async def test_websocket_feed_heartbeat_timeout_transitions_to_stale() -> None:
    """Feed transitions to STALE when heartbeat is not received within timeout."""

    async def silent_handler(websocket: ServerConnection) -> None:
        await websocket.recv()
        await websocket.recv()
        # Stay silent to trigger heartbeat timeout
        await asyncio.sleep(1.0)

    audit_store = InMemoryAuditEventStore()

    async with serve(silent_handler, "127.0.0.1", 0) as server:
        sockets = list(server.sockets)
        port = sockets[0].getsockname()[1]
        ws_url = f"ws://127.0.0.1:{port}"

        feed = CoinbaseMarketFeed(
            product_id="BTC-USD",
            ws_url=ws_url,
            audit_store=audit_store,
            heartbeat_timeout_seconds=0.1,  # Fast timeout for test
        )

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(stop))

        # Wait for timeout transition
        await asyncio.sleep(0.3)
        stop.set()
        await task

    events = await audit_store.list_recent(limit=20)
    actions = [e.action for e in events]
    assert "websocket_heartbeat_timeout" in actions


@pytest.mark.anyio
async def test_websocket_feed_reconnects_with_backoff_after_drop() -> None:
    """Feed reconnects after connection failure with exponential backoff."""
    connection_count = 0

    async def drop_handler(websocket: ServerConnection) -> None:
        nonlocal connection_count
        connection_count += 1
        if connection_count == 1:
            # First connection: read subscriptions then return, closing the socket.
            await websocket.recv()
            await websocket.recv()
            return
        # Second connection stays open quietly until stop
        await websocket.recv()
        await websocket.recv()
        await asyncio.sleep(2.0)

    audit_store = InMemoryAuditEventStore()

    async with serve(drop_handler, "127.0.0.1", 0) as server:
        sockets = list(server.sockets)
        port = sockets[0].getsockname()[1]
        ws_url = f"ws://127.0.0.1:{port}"

        feed = CoinbaseMarketFeed(
            product_id="BTC-USD",
            ws_url=ws_url,
            audit_store=audit_store,
            heartbeat_timeout_seconds=5.0,
        )

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(stop))

        # Wait for the reconnect to happen (first drop -> backoff -> retry)
        for _ in range(60):
            if connection_count >= 2:
                break
            await asyncio.sleep(0.05)
        stop.set()
        await task

    assert connection_count >= 2, "Feed must reconnect after a dropped connection"
    events = await audit_store.list_recent(limit=50)
    actions = [e.action for e in events]
    assert "websocket_connection_failed" in actions
    assert "websocket_state_reconnecting" in actions


@pytest.mark.anyio
async def test_websocket_feed_ignores_malformed_messages() -> None:
    """Invalid JSON and malformed ticker payloads are dropped without crashing."""
    received_tickers: list[TickerMessage] = []

    async def messy_handler(websocket: ServerConnection) -> None:
        await websocket.recv()
        await websocket.recv()
        # Invalid JSON
        await websocket.send("this is not json {{{")
        # Ticker channel with malformed event structure
        await websocket.send(json.dumps({"channel": "ticker", "events": "not-a-list"}))
        # Ticker with invalid decimal
        await websocket.send(
            json.dumps(
                {
                    "channel": "ticker",
                    "events": [
                        {
                            "type": "update",
                            "tickers": [
                                {
                                    "type": "ticker",
                                    "product_id": "BTC-USD",
                                    "price": "not-a-number",
                                    "volume_24_h": "100",
                                    "low_24_h": "1",
                                    "high_24_h": "2",
                                    "low_52_w": "1",
                                    "high_52_w": "2",
                                    "price_percent_chg_24_h": "0",
                                }
                            ],
                        }
                    ],
                }
            )
        )
        # Heartbeat with malformed counter
        await websocket.send(
            json.dumps({"channel": "heartbeats", "events": [{"heartbeat_counter": "NaN"}]})
        )
        # Valid ticker passes through after all the garbage
        await websocket.send(
            json.dumps(
                {
                    "channel": "ticker",
                    "events": [
                        {
                            "type": "update",
                            "tickers": [
                                {
                                    "type": "ticker",
                                    "product_id": "BTC-USD",
                                    "price": "65000.50",
                                    "volume_24_h": "12000.25",
                                    "low_24_h": "64000.00",
                                    "high_24_h": "66000.00",
                                    "low_52_w": "30000.00",
                                    "high_52_w": "70000.00",
                                    "price_percent_chg_24_h": "1.55",
                                }
                            ],
                        }
                    ],
                }
            )
        )
        await asyncio.sleep(1.0)

    audit_store = InMemoryAuditEventStore()

    async with serve(messy_handler, "127.0.0.1", 0) as server:
        sockets = list(server.sockets)
        port = sockets[0].getsockname()[1]
        ws_url = f"ws://127.0.0.1:{port}"

        feed = CoinbaseMarketFeed(
            product_id="BTC-USD",
            ws_url=ws_url,
            audit_store=audit_store,
            on_ticker=received_tickers.append,
            heartbeat_timeout_seconds=5.0,
        )

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(stop))

        for _ in range(40):
            if len(received_tickers) > 0:
                break
            await asyncio.sleep(0.05)
        stop.set()
        await task

    # Only the valid ticker parsed; feed stayed CONNECTED through the garbage
    assert len(received_tickers) == 1
    assert received_tickers[0].price == Decimal("65000.50")
    assert feed.state in {WebSocketConnectionState.CONNECTED, WebSocketConnectionState.DISCONNECTED}


@pytest.mark.anyio
async def test_websocket_feed_parses_heartbeat_messages() -> None:
    """Heartbeat channel events update last_heartbeat liveness evidence."""

    async def heartbeat_handler(websocket: ServerConnection) -> None:
        await websocket.recv()
        await websocket.recv()
        await websocket.send(
            json.dumps({"channel": "heartbeats", "events": [{"heartbeat_counter": 42}]})
        )
        await asyncio.sleep(1.0)

    audit_store = InMemoryAuditEventStore()

    async with serve(heartbeat_handler, "127.0.0.1", 0) as server:
        sockets = list(server.sockets)
        port = sockets[0].getsockname()[1]
        ws_url = f"ws://127.0.0.1:{port}"

        feed = CoinbaseMarketFeed(
            product_id="BTC-USD",
            ws_url=ws_url,
            audit_store=audit_store,
            heartbeat_timeout_seconds=5.0,
        )

        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(stop))

        for _ in range(40):
            if feed.last_heartbeat is not None:
                break
            await asyncio.sleep(0.05)
        stop.set()
        await task

    assert feed.last_heartbeat is not None
    assert feed.last_heartbeat.heartbeat_counter == 42


def test_websocket_feed_ignores_ticker_for_an_unsubscribed_product() -> None:
    """A validated ticker for another product cannot contaminate this feed's price."""
    received_tickers: list[TickerMessage] = []
    feed = CoinbaseMarketFeed(product_id="BTC-USD", on_ticker=received_tickers.append)

    feed._parse_ticker(
        {
            "product_id": "ETH-USD",
            "price": "999999",
            "volume_24_h": "1",
            "low_24_h": "1",
            "high_24_h": "2",
            "low_52_w": "1",
            "high_52_w": "2",
            "price_percent_chg_24_h": "0",
        }
    )

    assert feed.last_ticker is None
    assert received_tickers == []


def test_websocket_feed_rejects_heartbeat_without_counter() -> None:
    """Heartbeat frames must carry a validated counter before proving liveness."""
    feed = CoinbaseMarketFeed(product_id="BTC-USD")

    assert feed._parse_heartbeat({}) is False
    assert feed.last_heartbeat is None


@pytest.mark.anyio
async def test_websocket_feed_backoff_grows_across_consecutive_early_disconnects() -> None:
    """Connection drops before heartbeat proof use exponential retry delays."""
    connection_times: list[float] = []

    async def drop_handler(websocket: ServerConnection) -> None:
        connection_times.append(asyncio.get_running_loop().time())
        await websocket.recv()
        await websocket.recv()
        # Returning immediately closes before any heartbeat can prove liveness.

    async with serve(drop_handler, "127.0.0.1", 0) as server:
        sockets = list(server.sockets)
        feed = CoinbaseMarketFeed(
            product_id="BTC-USD",
            ws_url=f"ws://127.0.0.1:{sockets[0].getsockname()[1]}",
            heartbeat_timeout_seconds=5.0,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(stop))

        for _ in range(100):
            if len(connection_times) >= 3:
                break
            await asyncio.sleep(0.05)
        stop.set()
        await task

    assert len(connection_times) >= 3
    first_retry = connection_times[1] - connection_times[0]
    second_retry = connection_times[2] - connection_times[1]
    assert first_retry >= 0.9
    assert second_retry >= 1.8


@pytest.mark.anyio
async def test_websocket_feed_timeout_after_heartbeat_grows_backoff() -> None:
    """A stale session remains a retry failure even after one valid heartbeat."""
    connection_times: list[float] = []

    async def heartbeat_then_silence(websocket: ServerConnection) -> None:
        connection_times.append(asyncio.get_running_loop().time())
        await websocket.recv()
        await websocket.recv()
        await websocket.send(
            json.dumps({"channel": "heartbeats", "events": [{"heartbeat_counter": 1}]})
        )
        await asyncio.sleep(0.5)

    async with serve(heartbeat_then_silence, "127.0.0.1", 0) as server:
        sockets = list(server.sockets)
        feed = CoinbaseMarketFeed(
            product_id="BTC-USD",
            ws_url=f"ws://127.0.0.1:{sockets[0].getsockname()[1]}",
            heartbeat_timeout_seconds=0.1,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(stop))

        for _ in range(140):
            if len(connection_times) >= 3:
                break
            await asyncio.sleep(0.05)
        stop.set()
        await task

    assert len(connection_times) >= 3
    first_retry = connection_times[1] - connection_times[0]
    second_retry = connection_times[2] - connection_times[1]
    assert first_retry >= 1.0
    assert second_retry >= 1.9


@pytest.mark.anyio
async def test_websocket_feed_marks_stale_when_tickers_continue_without_heartbeats() -> None:
    """Ticker traffic cannot mask a missing heartbeat channel."""

    async def ticker_only_handler(websocket: ServerConnection) -> None:
        await websocket.recv()
        await websocket.recv()
        payload = json.dumps(
            {
                "channel": "ticker",
                "events": [
                    {
                        "tickers": [
                            {
                                "product_id": "BTC-USD",
                                "price": "65000.50",
                                "volume_24_h": "1",
                                "low_24_h": "1",
                                "high_24_h": "2",
                                "low_52_w": "1",
                                "high_52_w": "2",
                                "price_percent_chg_24_h": "0",
                            }
                        ]
                    }
                ],
            }
        )
        for _ in range(20):
            await websocket.send(payload)
            await asyncio.sleep(0.02)

    audit_store = InMemoryAuditEventStore()
    async with serve(ticker_only_handler, "127.0.0.1", 0) as server:
        sockets = list(server.sockets)
        feed = CoinbaseMarketFeed(
            product_id="BTC-USD",
            ws_url=f"ws://127.0.0.1:{sockets[0].getsockname()[1]}",
            audit_store=audit_store,
            heartbeat_timeout_seconds=0.1,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(stop))
        await asyncio.sleep(0.3)
        stop.set()
        await task

    actions = [event.action for event in await audit_store.list_recent(limit=50)]
    assert "websocket_heartbeat_timeout" in actions
