"""Coinbase Advanced Trade WebSocket public market feed connection manager."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from decimal import Decimal
import json
import logging
from typing import TYPE_CHECKING, Any

import websockets

from thytrader.exchanges.ws.models import (
    HeartbeatMessage,
    TickerMessage,
    WebSocketConnectionState,
)
from thytrader.persistence.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventStore,
)

if TYPE_CHECKING:
    from collections.abc import Callable

_logger = logging.getLogger(__name__)

# Default Coinbase Advanced Trade public WebSocket URL
COINBASE_WS_URL = "wss://advanced-trade-ws.coinbase.com"
DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 30.0
MAX_RECONNECT_BACKOFF_SECONDS = 60.0
INITIAL_RECONNECT_BACKOFF_SECONDS = 1.0


class CoinbaseMarketFeed:
    """Manages public market WebSocket connection, heartbeat, ticker parsing, and audit events."""

    def __init__(
        self,
        *,
        product_id: str = "BTC-USD",
        ws_url: str = COINBASE_WS_URL,
        audit_store: AuditEventStore | None = None,
        heartbeat_timeout_seconds: float = DEFAULT_HEARTBEAT_TIMEOUT_SECONDS,
        on_ticker: Callable[[TickerMessage], None] | None = None,
        on_state_changed: Callable[[WebSocketConnectionState], None] | None = None,
    ) -> None:
        """Initialize market feed configuration and event callbacks."""
        self._product_id = product_id
        self._ws_url = ws_url
        self._audit_store = audit_store
        self._heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self._on_ticker = on_ticker
        self._on_state_changed = on_state_changed

        self._state = WebSocketConnectionState.DISCONNECTED
        self._last_message_at: datetime | None = None
        self._last_ticker: TickerMessage | None = None
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
    def last_ticker(self) -> TickerMessage | None:
        """Most recent validated ticker tick."""
        return self._last_ticker

    @property
    def last_heartbeat(self) -> HeartbeatMessage | None:
        """Most recent validated heartbeat, proving connection liveness."""
        return self._last_heartbeat

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
                _logger.warning("WebSocket feed error: %s", exc)
                heartbeat_proven = self._heartbeat_observed_in_session
                await self._record_audit_event(
                    action="websocket_connection_failed",
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
        """Send subscription payload for ticker and heartbeat channels."""
        payload = {
            "type": "subscribe",
            "product_ids": [self._product_id],
            "channel": "ticker",
        }
        await ws.send(json.dumps(payload))
        heartbeat_payload = {
            "type": "subscribe",
            "product_ids": [self._product_id],
            "channel": "heartbeats",
        }
        await ws.send(json.dumps(heartbeat_payload))

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
        """Record stale state and report failure so reconnect backoff grows."""
        _logger.warning("WebSocket heartbeat timed out after %ss", self._heartbeat_timeout_seconds)
        await self._transition_state(WebSocketConnectionState.STALE)
        await self._record_audit_event(
            action="websocket_heartbeat_timeout",
            outcome=AuditEventOutcome.FAILURE,
            detail=f"Heartbeat timeout exceeding {self._heartbeat_timeout_seconds}s",
        )
        return False

    def _handle_message(self, raw_message: str | bytes) -> bool:
        """Parse one frame and return whether it contains a validated heartbeat.

        Structurally malformed payloads are dropped without raising so a
        hostile or buggy server cannot crash the listen loop.
        """
        try:
            payload = json.loads(raw_message)
            if not isinstance(payload, dict):
                _logger.warning("Received non-object WebSocket message")
                return False
            channel = payload.get("channel")
            events = payload.get("events", [])
            if not isinstance(events, list):
                _logger.warning("Received WebSocket message with non-list events")
                return False

            if channel == "ticker":
                self._dispatch_ticker_events(events)
                return False
            if channel == "heartbeats":
                return self._dispatch_heartbeat_events(events)
            return False  # noqa: TRY300 - unknown channels are intentionally ignored.
        except Exception:  # noqa: BLE001 - single messages must never crash the feed.
            _logger.warning("Failed to process WebSocket message")
            return False

    def _dispatch_ticker_events(self, events: list[Any]) -> None:
        """Parse ticker entries from a validated event list, skipping junk."""
        for event in events:
            if not isinstance(event, dict):
                continue
            tickers = event.get("tickers", [])
            if not isinstance(tickers, list):
                continue
            for tick_data in tickers:
                if isinstance(tick_data, dict):
                    self._parse_ticker(tick_data)

    def _dispatch_heartbeat_events(self, events: list[Any]) -> bool:
        """Parse heartbeat entries and report whether any validated successfully."""
        return any(self._parse_heartbeat(event) for event in events if isinstance(event, dict))

    def _parse_ticker(self, data: dict[str, Any]) -> None:
        """Parse ticker payload into TickerMessage."""
        try:
            prod_id = data.get("product_id", "")
            if prod_id != self._product_id:
                _logger.warning(
                    "Discarded ticker for unexpected product: expected=%s received=%s",
                    self._product_id,
                    prod_id,
                )
                return
            price_str = data.get("price", "0")
            vol_str = data.get("volume_24_h", "0")
            low_24_str = data.get("low_24_h", "0")
            high_24_str = data.get("high_24_h", "0")
            low_52_str = data.get("low_52_w", "0")
            high_52_str = data.get("high_52_w", "0")
            pct_chg_str = data.get("price_percent_chg_24_h", "0")

            ticker = TickerMessage(
                product_id=prod_id,
                price=Decimal(price_str),
                volume_24_h=Decimal(vol_str),
                low_24_h=Decimal(low_24_str),
                high_24_h=Decimal(high_24_str),
                low_52_w=Decimal(low_52_str),
                high_52_w=Decimal(high_52_str),
                price_percent_chg_24_h=Decimal(pct_chg_str),
                time=datetime.now(UTC),
            )
            self._last_ticker = ticker
            if self._on_ticker is not None:
                self._on_ticker(ticker)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Failed to parse ticker data: %s", exc)

    def _parse_heartbeat(self, data: dict[str, Any]) -> bool:
        """Parse a heartbeat event and report whether it validates.

        Malformed heartbeats are dropped without crashing the feed.  The
        caller alone advances the liveness deadline after a True result.
        """
        heartbeat_counter = data.get("heartbeat_counter")
        if not isinstance(heartbeat_counter, int) or isinstance(heartbeat_counter, bool):
            _logger.warning("Failed to parse heartbeat data: invalid heartbeat_counter")
            return False
        try:
            heartbeat = HeartbeatMessage(
                current_time=datetime.now(UTC),
                heartbeat_counter=heartbeat_counter,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Failed to parse heartbeat data: %s", exc.__class__.__name__)
            return False
        else:
            self._last_heartbeat = heartbeat
            return True

    async def _transition_state(self, new_state: WebSocketConnectionState) -> None:
        """Update connection state and record transition audit event."""
        if self._state == new_state:
            return
        old_state = self._state
        self._state = new_state
        _logger.info("WebSocket state transition: %s -> %s", old_state.value, new_state.value)
        if self._on_state_changed is not None:
            self._on_state_changed(new_state)
        outcome = (
            AuditEventOutcome.FAILURE
            if new_state == WebSocketConnectionState.STALE
            else AuditEventOutcome.INFO
        )
        await self._record_audit_event(
            action=f"websocket_state_{new_state.value}",
            outcome=outcome,
            detail=f"WebSocket transitioned from {old_state.value} to {new_state.value}",
        )

    async def _record_audit_event(
        self,
        *,
        action: str,
        outcome: AuditEventOutcome,
        detail: str,
    ) -> None:
        """Append an audit event safely."""
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
                product_id=self._product_id,
            )
            await self._audit_store.append(event)
        except Exception:
            _logger.exception("Failed to append WebSocket audit event")
