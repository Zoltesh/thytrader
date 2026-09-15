"""Authenticated Coinbase user-order feed supervision for the execution worker."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
import logging
from typing import TYPE_CHECKING

from thytrader.exchanges.ws.user_feed import COINBASE_USER_WS_URL, CoinbaseUserFeed
from thytrader.execution.user_feed_state import (
    UserOrderFeedSnapshot,
    UserOrderFeedState,
    UserOrderFeedStateStore,
    UserOrderFeedUnavailableError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from thytrader.exchanges.ws.models import WebSocketConnectionState
    from thytrader.exchanges.ws.user_feed import UserOrderObservation
    from thytrader.persistence.audit_events import AuditEventStore

_logger = logging.getLogger(__name__)
_SNAPSHOT_INTERVAL_SECONDS = 5.0


async def run_user_order_feed(
    stop_requested: asyncio.Event,
    *,
    enabled: bool,
    feed_store: UserOrderFeedStateStore,
    jwt_provider: Callable[[], str] | None = None,
    audit_store: AuditEventStore | None = None,
    ws_url: str = COINBASE_USER_WS_URL,
    heartbeat_timeout_seconds: float = 30.0,
    on_order_event: Callable[[UserOrderObservation], None] | None = None,
    wake_requested: asyncio.Event | None = None,
) -> None:
    """Run the user feed, or record that it is disabled without live credentials."""
    if not enabled or jwt_provider is None:
        await _record_disabled(feed_store)
        await stop_requested.wait()
        return

    persist_requested = asyncio.Event()
    persist_requested.set()

    def request_persist(_state: WebSocketConnectionState) -> None:
        persist_requested.set()

    def observe(observation: UserOrderObservation) -> None:
        if on_order_event is not None:
            on_order_event(observation)
        if wake_requested is not None:
            wake_requested.set()

    feed = CoinbaseUserFeed(
        jwt_provider=jwt_provider,
        ws_url=ws_url,
        audit_store=audit_store,
        heartbeat_timeout_seconds=heartbeat_timeout_seconds,
        on_order_event=observe,
        on_state_changed=request_persist,
    )
    persist_task = asyncio.create_task(
        _persist_loop(stop_requested, persist_requested, feed_store, feed)
    )
    try:
        await feed.run(stop_requested)
    finally:
        persist_requested.set()
        persist_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await persist_task
        await _persist_feed(feed_store, feed)


async def _persist_loop(
    stop_requested: asyncio.Event,
    persist_requested: asyncio.Event,
    store: UserOrderFeedStateStore,
    feed: CoinbaseUserFeed,
) -> None:
    """Persist feed snapshots on state change and a bounded interval."""
    while not stop_requested.is_set():
        persist_requested.clear()
        await _persist_feed(store, feed)
        waiter = asyncio.create_task(persist_requested.wait())
        stopper = asyncio.create_task(stop_requested.wait())
        _done, pending = await asyncio.wait(
            {waiter, stopper},
            timeout=_SNAPSHOT_INTERVAL_SECONDS,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()


async def _record_disabled(store: UserOrderFeedStateStore) -> None:
    """Persist an explicit disabled snapshot when live credentials are absent."""
    snapshot = UserOrderFeedSnapshot(
        state=UserOrderFeedState.DISABLED,
        updated_at=datetime.now(UTC),
    )
    try:
        await store.record(snapshot)
    except UserOrderFeedUnavailableError:
        _logger.warning("user_order_feed_state_unavailable")


async def _persist_feed(store: UserOrderFeedStateStore, feed: CoinbaseUserFeed) -> None:
    """Write the current in-memory user-feed facts without secrets."""
    snapshot = UserOrderFeedSnapshot(
        state=UserOrderFeedState(feed.state.value),
        last_message_at=feed.last_message_at,
        last_heartbeat_at=feed.last_heartbeat_at,
        updated_at=datetime.now(UTC),
    )
    try:
        await store.record(snapshot)
    except UserOrderFeedUnavailableError:
        _logger.warning("user_order_feed_state_unavailable")
