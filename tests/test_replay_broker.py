"""Scripted broker inputs are strict data, never a live network fallback."""

from __future__ import annotations

from decimal import Decimal
import importlib
from typing import TYPE_CHECKING, TypedDict

import pytest

from thytrader.execution.broker import SubmitResult
from thytrader.replay.broker import RecordingBroker, ScriptedBroker
from thytrader.replay.execution import OfflineBroker
from thytrader.trading.models import OrderKind, OrderSide, OrderStatus

if TYPE_CHECKING:
    from thytrader.market_data.models import Candle
    from thytrader.trading.models import Fill, Order


class PlaceArguments(TypedDict):
    """Typed arguments passed through the public broker contract in this test."""

    client_order_id: str
    product_id: str
    side: OrderSide
    kind: OrderKind
    quantity: Decimal
    price: Decimal


class SyntheticBroker(OfflineBroker):
    """Independent synthetic venue with a deterministic acknowledgement."""

    async def place_order(
        self,
        *,
        client_order_id: str,
        product_id: str,
        side: OrderSide,
        kind: OrderKind,
        quantity: Decimal,
        price: Decimal | None,
        stop_trigger_price: Decimal | None = None,
        take_profit_price: Decimal | None = None,
    ) -> SubmitResult:
        """Acknowledge without fills, fees or a real-order transport."""
        del client_order_id, product_id, side, kind, quantity, price
        del stop_trigger_price, take_profit_price
        return SubmitResult(OrderStatus.OPEN, "synthetic-order")

    def match_open_order(self, order: Order, candle: Candle) -> Fill | None:
        """Live venue fills arrive via canonical list_fills, never candle matching."""
        del order, candle
        return None

    def maker_limit_price(
        self, *, product_id: str, mark: Decimal, side: OrderSide = OrderSide.BUY
    ) -> Decimal:
        """Use a disclosed synthetic quote, never claim it was observed live."""
        del product_id, side
        return mark


@pytest.mark.anyio
async def test_scripted_broker_rejects_changed_request_and_consumes_all_events() -> None:
    """Recorded input/response causality is enforced before a response can be replayed."""
    module = importlib.import_module("thytrader.replay.execution")
    assert hasattr(module, "RecordingBroker"), "normalized broker observation is missing"

    recording = RecordingBroker(SyntheticBroker())
    arguments: PlaceArguments = {
        "client_order_id": "synthetic-client",
        "product_id": "BTC-USD",
        "side": OrderSide.BUY,
        "kind": OrderKind.POST_ONLY_LIMIT,
        "quantity": Decimal("2"),
        "price": Decimal("10"),
    }
    observed = await recording.place_order(**arguments)
    replay = ScriptedBroker(recording.events)
    assert await replay.place_order(**arguments) == observed
    assert replay.complete
    changed = ScriptedBroker(recording.events)
    arguments["quantity"] = Decimal("3")
    with pytest.raises(ValueError, match="request"):
        await changed.place_order(**arguments)


@pytest.mark.anyio
async def test_recording_preserves_original_broker_failure_identity() -> None:
    """Observation cannot replace the venue exception or change timeout recovery semantics."""
    failure = TimeoutError("synthetic timeout detail")

    class FailingBroker(SyntheticBroker):
        """Synthetic read failure with a known exception instance."""

        async def get_order(self, *, venue_order_id: str, client_order_id: str) -> SubmitResult:
            """Raise the original simulated venue failure."""
            del venue_order_id, client_order_id
            raise failure

    broker = RecordingBroker(FailingBroker())
    with pytest.raises(TimeoutError) as caught:
        await broker.get_order(venue_order_id="synthetic-order", client_order_id="synthetic-client")
    assert caught.value is failure
    assert broker.events[0].error == "TimeoutError"
