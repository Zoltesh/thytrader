"""Adversarial dormant-adapter boundaries beyond ordinary venue fixtures."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal, localcontext
from typing import TYPE_CHECKING, Any

import pytest

from tests.exchanges.test_coinbase_broker import FakeTransport
from tests.exchanges.test_coinbase_futures_broker import (
    CLIENT,
    HISTORICAL,
    ORDERS,
    PRODUCT,
    Sizes,
    order_json,
)
from tests.exchanges.test_futures_requests import place
from thytrader.exchanges.coinbase_futures_broker import CoinbaseFuturesBroker
from thytrader.exchanges.rest_transport import CoinbaseHttpStatusError
from thytrader.execution.broker import BrokerError
from thytrader.trading.models import OrderKind, OrderSide, OrderStatus

if TYPE_CHECKING:
    from collections.abc import Mapping


@pytest.mark.anyio
@pytest.mark.parametrize("changes", [{"client_order_id": None}, {"product_id": None}])
async def test_malformed_runtime_identifiers_are_no_io_refusals(changes: dict[str, Any]) -> None:
    """Annotations do not validate accidentally loaded null ids."""
    transport = FakeTransport(gets={})
    assert (await place(transport, **changes)).status is OrderStatus.REJECTED
    assert transport.calls == []


@pytest.mark.anyio
@pytest.mark.parametrize("quantity", [Decimal("1E+999999"), Decimal("1E-999999")])
async def test_extreme_contract_division_denies_with_all_traps_disabled(quantity: Decimal) -> None:
    """Overflow saturation and underflow cannot pass as a rounded integer count."""
    transport = FakeTransport(gets={})
    with localcontext() as context:
        context.clear_traps()
        context.rounding = ROUND_DOWN
        context.prec = 2
        context.Emax = 10
        context.Emin = -10
        result = await place(transport, quantity=quantity)
    assert result.status is OrderStatus.REJECTED
    assert transport.calls == []


@pytest.mark.anyio
async def test_contradictory_acknowledgement_remains_unknown() -> None:
    """An order identity alongside success:false is not proof that no order exists."""
    transport = FakeTransport(
        gets={},
        posts={
            ORDERS: [
                {
                    "success": False,
                    "success_response": {"order_id": "venue-test"},
                    "error_response": {"error": "UNKNOWN"},
                }
            ]
        },
    )
    result = await place(transport)
    assert result.status is OrderStatus.UNKNOWN
    assert result.venue_order_id == "venue-test"


@pytest.mark.anyio
async def test_filled_status_requires_positive_contracts() -> None:
    """FILLED plus zero units is contradictory, not safe financial evidence."""
    transport = FakeTransport(gets={HISTORICAL: [{"order": order_json(filled_size="0")}]})
    with pytest.raises(BrokerError):
        await CoinbaseFuturesBroker(transport, Sizes()).get_order(
            venue_order_id="venue-test", client_order_id=CLIENT
        )


class _NotFoundTransport(FakeTransport):
    """Answer every POST as the venue answered a close without a matching position."""

    def post(self, path: str, data: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Record the call, then raise the observed HTTP 404 ``NOT_FOUND``."""
        self.calls.append(("POST", path, dict(data or {})))
        raise CoinbaseHttpStatusError(404, "NOT_FOUND")


@pytest.mark.anyio
async def test_default_close_is_sized_and_does_not_fallback_on_rejection() -> None:
    """A close without a matching position is a definite rejection; no unsized retry or fallback."""
    client = CLIENT.replace(":entry:", ":stop:")
    path = ORDERS + "/close_position"
    transport = _NotFoundTransport(gets={})
    result = await CoinbaseFuturesBroker(transport, Sizes()).place_order(
        client_order_id=client,
        product_id=PRODUCT,
        side=OrderSide.SELL,
        kind=OrderKind.MARKETABLE,
        quantity=Decimal("0.1"),
        price=None,
    )
    assert result.status is OrderStatus.REJECTED
    assert result.reject_reason == "coinbase_http_404:NOT_FOUND"
    assert transport.calls == [
        ("POST", path, {"client_order_id": client, "product_id": PRODUCT, "size": "1"})
    ]


@pytest.mark.anyio
async def test_market_entry_and_adoption_are_rejected() -> None:
    """The close_position path confers no authority for a market entry or adoption."""
    transport = FakeTransport(gets={})
    for purpose in ("entry", "adoption"):
        result = await CoinbaseFuturesBroker(transport, Sizes()).place_order(
            client_order_id=CLIENT.replace(":entry:", f":{purpose}:"),
            product_id=PRODUCT,
            side=OrderSide.BUY,
            kind=OrderKind.MARKETABLE,
            quantity=Decimal("0.1"),
            price=None,
        )
        assert result.status is OrderStatus.REJECTED
    assert transport.calls == []
