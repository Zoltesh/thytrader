"""Preserve the optional read-only client lookup capability during observation and replay."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution.broker import ClientOrderLookup, SubmitResult
from thytrader.replay.broker import RecordingBroker, ScriptedBroker, raise_recorded_error
from thytrader.replay.broker_events import BrokerRequest

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from thytrader.execution.broker import Broker
    from thytrader.replay.broker_events import BrokerEvent


class RecordingLookupBroker(RecordingBroker):
    """Only constructed for a delegate whose existing contract supports bounded lookup."""

    async def find_order_by_client_id(
        self, *, client_order_id: str, product_id: str, submitted_at: datetime
    ) -> SubmitResult | None:
        """Observe one original read without inventing absence or resending an order."""
        delegate = self.delegate
        if not isinstance(delegate, ClientOrderLookup):
            raise TypeError("client-order lookup capability is unavailable")
        request = BrokerRequest(
            client_order_id=client_order_id, product_id=product_id, submitted_at=submitted_at
        )
        event = await self.record(
            "lookup",
            request,
            lambda: delegate.find_order_by_client_id(
                client_order_id=client_order_id, product_id=product_id, submitted_at=submitted_at
            ),
        )
        return event.result


class ScriptedLookupBroker(ScriptedBroker):
    """Optional capability driven solely by an explicit captured capability flag and input tape."""

    async def find_order_by_client_id(
        self, *, client_order_id: str, product_id: str, submitted_at: datetime
    ) -> SubmitResult | None:
        """A recorded None is a bounded-scan result, not fabricated missing evidence."""
        event = self.take(
            "lookup",
            BrokerRequest(
                client_order_id=client_order_id, product_id=product_id, submitted_at=submitted_at
            ),
        )
        raise_recorded_error(event)
        return event.result


def observe_broker(delegate: Broker) -> RecordingBroker:
    """Retain runtime protocol conformance, including absence of the optional lookup method."""
    if isinstance(delegate, ClientOrderLookup):
        return RecordingLookupBroker(delegate)
    return RecordingBroker(delegate)


def replay_broker(events: Sequence[BrokerEvent], *, supports_client_lookup: bool) -> ScriptedBroker:
    """Never construct an exchange client; capability selection is explicit immutable input data."""
    if supports_client_lookup:
        return ScriptedLookupBroker(events)
    return ScriptedBroker(events)
