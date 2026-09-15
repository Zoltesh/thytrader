"""Coinbase Advanced Trade authenticated user-channel WebSocket manager."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
from typing import TYPE_CHECKING, Any

import websockets

from thytrader.exchanges.ws.market_feed import (
    DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
    INITIAL_RECONNECT_BACKOFF_SECONDS,
    MAX_RECONNECT_BACKOFF_SECONDS,
)
from thytrader.exchanges.ws.models import HeartbeatMessage, WebSocketConnectionState
from thytrader.persistence.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventStore,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_logger = logging.getLogger(__name__)

COINBASE_USER_WS_URL = "wss://advanced-trade-ws-user.coinbase.com"


@dataclass(frozen=True, slots=True)
class UserOrderObservation:
    """One observed user-channel order identity used to nudge REST reconcile."""

    client_order_id: str
    venue_order_id: str | None
    status: str | None


class CoinbaseUserFeed:
    """Manages the authenticated user channel, heartbeat, and reconnect backoff."""

    def __init__(
        self,
        *,
        jwt_provider: Callable[[], str],
        ws_url: str = COINBASE_USER_WS_URL,
        audit_store: AuditEventStore | None = None,
        heartbeat_timeout_seconds: float = DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
        on_order_event: Callable[[UserOrderObservation], None] | None = None,
        on_state_changed: Callable[[WebSocketConnectionState], None] | None = None,
    ) -> None:
        """Initialize user-feed configuration without retaining JWT material."""
        self._jwt_provider = jwt_provider
        self._ws_url = ws_url
        self._audit_store = audit_store
        self._heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self._on_order_event = on_order_event
        self._on_state_changed = on_state_changed
        self._state = WebSocketConnectionState.DISCONNECTED
        self._last_message_at: datetime | None = None
        self._last_heartbeat: HeartbeatMessage | None = None
        self._last_heartbeat_at: datetime | None = None
        self._heartbeat_observed_in_session = False

    @property
    def state(self) -> WebSocketConnectionState:
        """Current lifecycle connection state."""
        return self._state

    @property
    def last_message_at(self) -> datetime | None:
        """Timestamp of the most recent message or heartbeat received."""
        return self._last_message_at

    @property
    def last_heartbeat_at(self) -> datetime | None:
        """Timestamp of the most recent validated heartbeat."""
        return self._last_heartbeat_at

    async def run(self, stop_requested: asyncio.Event) -> None:
        """Run the feed lifecycle until stop_requested is set."""
        backoff = INITIAL_RECONNECT_BACKOFF_SECONDS
        while not stop_requested.is_set():
            heartbeat_proven = False
            self._heartbeat_observed_in_session = False
            try:
                await self._transition_state(WebSocketConnectionState.CONNECTING)
                async with websockets.connect(self._ws_url) as ws:
                    await self._transition_state(WebSocketConnectionState.CONNECTED)
                    await self._subscribe(ws)
                    heartbeat_proven = await self._listen(ws, stop_requested)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                _logger.warning("User WebSocket feed error: %s", exc.__class__.__name__)
                heartbeat_proven = self._heartbeat_observed_in_session
                await self._record_audit_event(
                    action="user_websocket_connection_failed",
                    outcome=AuditEventOutcome.FAILURE,
                    detail=f"Connection failure: {exc.__class__.__name__}",
                )
            if heartbeat_proven:
                backoff = INITIAL_RECONNECT_BACKOFF_SECONDS
            if stop_requested.is_set():
                break
            await self._transition_state(WebSocketConnectionState.RECONNECTING)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop_requested.wait(), timeout=backoff)
            backoff = min(backoff * 2.0, MAX_RECONNECT_BACKOFF_SECONDS)
        await self._transition_state(WebSocketConnectionState.DISCONNECTED)

    async def _subscribe(self, ws: Any) -> None:
        """Subscribe to the user and heartbeat channels with a short-lived JWT."""
        token = self._jwt_provider()
        await ws.send(json.dumps({"type": "subscribe", "channel": "user", "jwt": token}))
        await ws.send(json.dumps({"type": "subscribe", "channel": "heartbeats", "jwt": token}))

    async def _listen(self, ws: Any, stop_requested: asyncio.Event) -> bool:
        """Listen until stop, close, or a validated heartbeat becomes overdue."""
        now = datetime.now(UTC)
        self._last_message_at = now
        self._last_heartbeat_at = now
        while not stop_requested.is_set():
            last_heartbeat_at = self._last_heartbeat_at
            if last_heartbeat_at is None:
                return await self._mark_heartbeat_timeout()
            remaining = (
                self._heartbeat_timeout_seconds
                - (datetime.now(UTC) - last_heartbeat_at).total_seconds()
            )
            if remaining <= 0:
                return await self._mark_heartbeat_timeout()
            try:
                raw_message = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except TimeoutError:
                return await self._mark_heartbeat_timeout()
            received_at = datetime.now(UTC)
            self._last_message_at = received_at
            if self._handle_message(raw_message):
                last_heartbeat_at = received_at
                self._last_heartbeat_at = received_at
                self._heartbeat_observed_in_session = True
            if (received_at - last_heartbeat_at).total_seconds() >= self._heartbeat_timeout_seconds:
                return await self._mark_heartbeat_timeout()
        return self._heartbeat_observed_in_session

    async def _mark_heartbeat_timeout(self) -> bool:
        """Record stale state so 5m live can pause on an unproven feed."""
        _logger.warning(
            "User WebSocket heartbeat timed out after %ss", self._heartbeat_timeout_seconds
        )
        await self._transition_state(WebSocketConnectionState.STALE)
        await self._record_audit_event(
            action="user_websocket_heartbeat_timeout",
            outcome=AuditEventOutcome.FAILURE,
            detail=f"Heartbeat timeout exceeding {self._heartbeat_timeout_seconds}s",
        )
        return False

    def _handle_message(self, raw_message: str | bytes) -> bool:
        """Parse one frame and return whether it contains a validated heartbeat."""
        try:
            payload = json.loads(raw_message)
            if not isinstance(payload, dict):
                return False
            channel = payload.get("channel")
            events = payload.get("events", [])
            if not isinstance(events, list):
                return False
            if channel == "user":
                self._dispatch_order_events(events)
                return False
            if channel == "heartbeats":
                return any(
                    self._parse_heartbeat(event) for event in events if isinstance(event, dict)
                )
            return False  # noqa: TRY300 - unknown channels are intentionally ignored.
        except Exception:  # noqa: BLE001 - a single frame must never crash the feed.
            _logger.warning("Failed to process user WebSocket message")
            return False

    def _dispatch_order_events(self, events: list[Any]) -> None:
        """Surface client_order_id observations without treating them as fills."""
        if self._on_order_event is None:
            return
        for event in events:
            if not isinstance(event, dict):
                continue
            orders = event.get("orders", [])
            if not isinstance(orders, list):
                continue
            for item in orders:
                if not isinstance(item, dict):
                    continue
                client_order_id = item.get("client_order_id")
                if not isinstance(client_order_id, str) or not client_order_id:
                    continue
                venue = item.get("order_id")
                status = item.get("status")
                self._on_order_event(
                    UserOrderObservation(
                        client_order_id=client_order_id,
                        venue_order_id=venue if isinstance(venue, str) else None,
                        status=status if isinstance(status, str) else None,
                    )
                )

    def _parse_heartbeat(self, data: dict[str, Any]) -> bool:
        """Parse a heartbeat event and report whether it validates."""
        heartbeat_counter = data.get("heartbeat_counter")
        if not isinstance(heartbeat_counter, int) or isinstance(heartbeat_counter, bool):
            return False
        try:
            heartbeat = HeartbeatMessage(
                current_time=datetime.now(UTC),
                heartbeat_counter=heartbeat_counter,
            )
        except Exception:  # noqa: BLE001
            return False
        self._last_heartbeat = heartbeat
        return True

    async def _transition_state(self, new_state: WebSocketConnectionState) -> None:
        """Update connection state and record a redacted transition audit event."""
        if self._state == new_state:
            return
        old_state = self._state
        self._state = new_state
        _logger.info("User WebSocket state transition: %s -> %s", old_state.value, new_state.value)
        if self._on_state_changed is not None:
            self._on_state_changed(new_state)
        outcome = (
            AuditEventOutcome.FAILURE
            if new_state == WebSocketConnectionState.STALE
            else AuditEventOutcome.INFO
        )
        await self._record_audit_event(
            action=f"user_websocket_state_{new_state.value}",
            outcome=outcome,
            detail=f"User WebSocket transitioned from {old_state.value} to {new_state.value}",
        )

    async def _record_audit_event(
        self,
        *,
        action: str,
        outcome: AuditEventOutcome,
        detail: str,
    ) -> None:
        """Append an audit event without JWT or credential material."""
        if self._audit_store is None:
            return
        try:
            event = AuditEvent(
                occurred_at=datetime.now(UTC),
                category=AuditEventCategory.WEBSOCKET,
                action=action,
                outcome=outcome,
                detail=detail,
                provider="coinbase",
            )
            await self._audit_store.append(event)
        except Exception:
            _logger.exception("Failed to append user WebSocket audit event")
