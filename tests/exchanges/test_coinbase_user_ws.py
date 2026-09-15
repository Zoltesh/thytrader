"""Authenticated Coinbase user-channel WebSocket observations are not the fill ledger."""

import asyncio
import json

import pytest
from websockets.asyncio.server import ServerConnection, serve

from thytrader.exchanges.ws.models import WebSocketConnectionState
from thytrader.exchanges.ws.user_feed import CoinbaseUserFeed, UserOrderObservation
from thytrader.persistence.audit_events import AuditEventCategory, InMemoryAuditEventStore


@pytest.mark.anyio
async def test_user_feed_subscribes_with_jwt_and_observes_orders() -> None:
    """The user feed authenticates with JWT and surfaces client_order_id observations."""
    received: list[UserOrderObservation] = []
    subscribed: list[dict[str, object]] = []

    async def fake_handler(websocket: ServerConnection) -> None:
        subscribed.append(json.loads(await websocket.recv()))
        subscribed.append(json.loads(await websocket.recv()))
        await websocket.send(
            json.dumps(
                {
                    "channel": "heartbeats",
                    "events": [{"heartbeat_counter": 1}],
                }
            )
        )
        await websocket.send(
            json.dumps(
                {
                    "channel": "user",
                    "events": [
                        {
                            "orders": [
                                {
                                    "client_order_id": "client-1",
                                    "order_id": "venue-1",
                                    "status": "OPEN",
                                }
                            ]
                        }
                    ],
                }
            )
        )
        await asyncio.sleep(0.5)

    audit_store = InMemoryAuditEventStore()
    async with serve(fake_handler, "127.0.0.1", 0) as server:
        sockets = list(server.sockets)
        port = sockets[0].getsockname()[1]
        feed = CoinbaseUserFeed(
            jwt_provider=lambda: "test-jwt",
            ws_url=f"ws://127.0.0.1:{port}",
            audit_store=audit_store,
            on_order_event=received.append,
            heartbeat_timeout_seconds=2.0,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(feed.run(stop))
        for _ in range(40):
            if received:
                break
            await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    assert [item.get("channel") for item in subscribed] == ["user", "heartbeats"]
    assert all(item.get("jwt") == "test-jwt" for item in subscribed)
    assert received == [
        UserOrderObservation(
            client_order_id="client-1",
            venue_order_id="venue-1",
            status="OPEN",
        )
    ]
    assert feed.state in {
        WebSocketConnectionState.DISCONNECTED,
        WebSocketConnectionState.RECONNECTING,
    }
    events = await audit_store.list_recent(limit=20)
    actions = {event.action for event in events}
    assert any(action.startswith("user_websocket_state_") for action in actions)
    assert all(event.category is AuditEventCategory.WEBSOCKET for event in events)
    assert all("jwt" not in (event.detail or "").lower() for event in events)
    assert all("test-jwt" not in (event.detail or "") for event in events)
