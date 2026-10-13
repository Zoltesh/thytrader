"""Broker evidence must not erase optional safety capabilities or certify consumed-prefix errors."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from thytrader.execution.broker import ClientOrderLookup, SubmitResult
from thytrader.replay.broker import ScriptedBroker
from thytrader.replay.broker_events import BrokerRequest
from thytrader.replay.execution import OfflineBroker
from thytrader.replay.lookup import observe_broker, replay_broker


def test_swallowed_unexpected_request_cannot_certify_complete_tape() -> None:
    """Even if the execution engine handles a refusal, the evidence tape stays poisoned."""
    broker = ScriptedBroker(())
    with pytest.raises(ValueError, match="recorded input"):
        broker.take("get", BrokerRequest(client_order_id="fixture", venue_order_id="fixture"))
    assert not broker.complete


class LookupVenue(OfflineBroker):
    """Independent read-only ambiguous-order lookup; never has submit authority."""

    async def find_order_by_client_id(
        self, *, client_order_id: str, product_id: str, submitted_at: datetime
    ) -> SubmitResult | None:
        """Return the observed bounded-scan absence without retrying a submit."""
        del client_order_id, product_id, submitted_at
        return None


@pytest.mark.anyio
async def test_capture_preserves_optional_lookup_without_advertising_it_when_absent() -> None:
    """Protocol capability tests remain identical before and after observation wrapping."""
    assert not isinstance(observe_broker(OfflineBroker()), ClientOrderLookup)
    recorded = observe_broker(LookupVenue())
    assert isinstance(recorded, ClientOrderLookup)
    submitted_at = datetime(2025, 1, 1, tzinfo=UTC)
    result = await recorded.find_order_by_client_id(
        client_order_id="independent", product_id="FIXTURE-USD", submitted_at=submitted_at
    )
    assert result is None
    assert len(recorded.events) == 1
    replay = replay_broker(recorded.events, supports_client_lookup=True)
    assert isinstance(replay, ClientOrderLookup)
    assert (
        await replay.find_order_by_client_id(
            client_order_id="independent", product_id="FIXTURE-USD", submitted_at=submitted_at
        )
        is None
    )
    assert replay.complete
