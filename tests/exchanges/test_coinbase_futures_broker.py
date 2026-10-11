"""Dormant futures adapter tests use synthetic JSON and never a real venue."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from tests.exchanges.test_coinbase_broker import FakeTransport
from thytrader.exchanges.coinbase_futures_broker import CoinbaseFuturesBroker
from thytrader.trading.models import OrderKind, OrderSide, OrderStatus

PRODUCT = "ETP-20DEC30-CDE"
CLIENT = (
    "00000000-0000-4000-8000-000000000001:entry:20260105T1200:00000000-0000-7000-8000-000000000002"
)
ORDERS = "/api/v3/brokerage/orders"
HISTORICAL = ORDERS + "/historical/venue-test"


class Sizes:
    """Supply synthetic catalog sizes without transport calls."""

    def __init__(self, size: Decimal | None = Decimal("0.1")) -> None:
        """Bind one test catalog observation."""
        self.size = size

    async def contract_size(self, product_id: str) -> Decimal | None:
        """Return a known contract size only for the fixture product."""
        return self.size if product_id == PRODUCT else None


def order_json(**changes: Any) -> dict[str, Any]:
    """Construct untrusted venue JSON, with overrides for malformed-boundary tests."""
    return {
        "order_id": "venue-test",
        "product_id": PRODUCT,
        "product_type": "FUTURE",
        "client_order_id": CLIENT,
        "status": "FILLED",
        "filled_size": "2",
        "average_filled_price": "2500",
        "size_in_quote": False,
        **changes,
    }


@pytest.mark.anyio
@pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL])
async def test_post_only_entry_round_trips_contract_units(side: OrderSide) -> None:
    """One tracer bullet converts base to contracts, submits, then observes base units."""
    transport = FakeTransport(
        posts={ORDERS: [{"success": True, "success_response": {"order_id": "venue-test"}}]},
        gets={HISTORICAL: [{"order": order_json()}]},
    )
    result = await CoinbaseFuturesBroker(transport, Sizes()).place_order(
        client_order_id=CLIENT,
        product_id=PRODUCT,
        side=side,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.2"),
        price=Decimal("2500"),
    )
    assert result.status is OrderStatus.FILLED
    assert result.filled_quantity == Decimal("0.2")
    assert transport.calls[0] == (
        "POST",
        ORDERS,
        {
            "client_order_id": CLIENT,
            "product_id": PRODUCT,
            "side": side.value.upper(),
            "order_configuration": {
                "limit_limit_gtc": {
                    "base_size": "2",
                    "limit_price": "2500",
                    "post_only": True,
                }
            },
        },
    )
