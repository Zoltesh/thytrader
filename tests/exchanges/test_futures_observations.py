"""Ambiguous submits, strict snapshots, bounded recovery and cancellation."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, localcontext
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
from thytrader.execution.broker import BrokerError, ClientOrderLookup
from thytrader.trading.models import OrderStatus

if TYPE_CHECKING:
    from collections.abc import Mapping

BATCH = ORDERS + "/historical/batch"
CANCEL = ORDERS + "/batch_cancel"


class FailingTransport(FakeTransport):
    """Raise a synthetic failure after recording the create attempt."""

    def __init__(self, error: Exception) -> None:
        """Bind one error without contacting any network."""
        super().__init__(gets={})
        self.error = error

    def post(self, path: str, data: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Record then fail so tests distinguish no-send refusals from unknown submits."""
        self.calls.append(("POST", path, dict(data or {})))
        raise self.error


@pytest.mark.anyio
@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
async def test_definite_http_refusals_are_rejected(code: int) -> None:
    """Only documented definite HTTP failures may become REJECTED."""
    transport = FailingTransport(
        CoinbaseHttpStatusError(code, error_code="INVALID_FCM_TRADING_SESSION")
    )
    result = await place(transport)
    assert result.status is OrderStatus.REJECTED
    assert result.reject_reason == f"coinbase_http_{code}:INVALID_FCM_TRADING_SESSION"
    assert len(transport.calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(),
        OSError(),
        CoinbaseHttpStatusError(408, error_code=None),
        CoinbaseHttpStatusError(409, error_code=None),
        CoinbaseHttpStatusError(429, error_code=None),
        CoinbaseHttpStatusError(500, error_code=None),
    ],
)
async def test_uncertain_send_raises_for_existing_unknown_reconciliation(error: Exception) -> None:
    """No retries and no automatic switch to another reduction mode."""
    transport = FailingTransport(error)
    with pytest.raises(BrokerError):
        await place(transport)
    assert len(transport.calls) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [{}, {"success": True}, {"success": "true"}, {"success": True, "success_response": []}],
)
async def test_malformed_ack_is_unknown_not_definitely_rejected(payload: dict[str, Any]) -> None:
    """A missing acknowledgement cannot prove that the venue did not create the order."""
    result = await place(FakeTransport(gets={}, posts={ORDERS: [payload]}))
    assert result.status is OrderStatus.UNKNOWN


@pytest.mark.anyio
async def test_acknowledged_order_with_bad_observation_is_unknown() -> None:
    """Retain known venue identity when the observation cannot establish its state."""
    transport = FakeTransport(
        gets={HISTORICAL: [{"order": {}}]},
        posts={ORDERS: [{"success": True, "success_response": {"order_id": "venue-test"}}]},
    )
    result = await place(transport)
    assert result.status is OrderStatus.UNKNOWN
    assert result.venue_order_id == "venue-test"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "changes",
    [
        {"product_type": "SPOT"},
        {"product_type": None},
        {"product_id": "BTC-USD"},
        {"order_id": "wrong"},
        {"client_order_id": "wrong"},
        {"filled_size": None},
        {"filled_size": "1.5"},
        {"filled_size": "NaN"},
        {"filled_size": "-1"},
        {"filled_size": 1.0},
        {"size_in_quote": True},
        {"size_in_quote": "false"},
        {"average_filled_price": "NaN"},
        {"average_filled_price": "0"},
    ],
)
async def test_invalid_order_observation_is_not_a_snapshot(changes: dict[str, Any]) -> None:
    """Malformed units/identity cannot silently become zero-filled or another order."""
    transport = FakeTransport(gets={HISTORICAL: [{"order": order_json(**changes)}]})
    with pytest.raises(BrokerError):
        await CoinbaseFuturesBroker(transport, Sizes()).get_order(
            venue_order_id="venue-test", client_order_id=CLIENT
        )


@pytest.mark.anyio
async def test_inexact_observed_quantity_fails_closed() -> None:
    """Observed multiplication cannot round even with caller traps disabled."""
    transport = FakeTransport(gets={HISTORICAL: [{"order": order_json(filled_size="3")}]})
    with localcontext() as context:
        context.prec = 2
        context.clear_traps()
        with pytest.raises(BrokerError):
            await CoinbaseFuturesBroker(transport, Sizes(Decimal("0.123"))).get_order(
                venue_order_id="venue-test", client_order_id=CLIENT
            )


@pytest.mark.anyio
async def test_futures_lookup_filters_and_paginates() -> None:
    """Recovery is FUTURE-only, time bounded, and returns domain base quantities."""
    transport = FakeTransport(
        gets={
            BATCH: [
                {"orders": [], "has_next": True, "cursor": "next"},
                {"orders": [order_json()], "has_next": False},
            ],
            HISTORICAL: [{"order": order_json()}],
        }
    )
    broker = CoinbaseFuturesBroker(transport, Sizes())
    assert isinstance(broker, ClientOrderLookup)
    result = await broker.find_order_by_client_id(
        client_order_id=CLIENT,
        product_id=PRODUCT,
        submitted_at=datetime(2026, 1, 5, 12, tzinfo=UTC),
    )
    assert result is not None and result.filled_quantity == Decimal("0.2")
    assert transport.calls[0][2] == {
        "product_ids": [PRODUCT],
        "product_type": "FUTURE",
        "start_date": "2026-01-05T11:55:00Z",
        "limit": 100,
    }
    assert transport.calls[1][2]["cursor"] == "next"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "pages",
    [
        [{}],
        [{"orders": [None]}],
        [{"orders": [], "has_next": True}],
        [{"orders": [], "has_next": True, "cursor": "repeat"}] * 2,
        [{"orders": [], "has_next": True, "cursor": str(i)} for i in range(20)],
        [{"orders": [order_json(product_id="BTC-USD")], "has_next": False}],
    ],
)
async def test_incomplete_lookup_is_not_absence(pages: list[dict[str, Any]]) -> None:
    """Bad pages and wrong-product matches must keep reconciliation unresolved."""
    broker = CoinbaseFuturesBroker(FakeTransport(gets={BATCH: pages}), Sizes())
    assert isinstance(broker, ClientOrderLookup)
    with pytest.raises(BrokerError):
        await broker.find_order_by_client_id(
            client_order_id=CLIENT,
            product_id=PRODUCT,
            submitted_at=datetime(2026, 1, 5, tzinfo=UTC),
        )


@pytest.mark.anyio
async def test_complete_lookup_can_prove_absence() -> None:
    """Only an explicit complete page means absent; no POST is ever used."""
    transport = FakeTransport(gets={BATCH: [{"orders": [], "has_next": False}]})
    broker = CoinbaseFuturesBroker(transport, Sizes())
    assert isinstance(broker, ClientOrderLookup)
    assert (
        await broker.find_order_by_client_id(
            client_order_id=CLIENT,
            product_id=PRODUCT,
            submitted_at=datetime(2026, 1, 5, tzinfo=UTC),
        )
        is None
    )
    assert [call[0] for call in transport.calls] == ["GET"]


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["CANCELLED", "OPEN"])
async def test_cancel_confirms_bounded_observation(status: str) -> None:
    """Cancel acceptance is not cancellation; pending orders retain active state."""
    row = order_json(status=status, filled_size="0", average_filled_price="0")
    transport = FakeTransport(
        gets={
            HISTORICAL: [{"order": order_json(status="OPEN", filled_size="0")}]
            + [{"order": row}] * 3
        },
        posts={CANCEL: [{"results": [{"order_id": "venue-test", "success": True}]}]},
    )
    broker = CoinbaseFuturesBroker(transport, Sizes())
    assert hasattr(broker, "cancel_order")
    result = await broker.cancel_order(venue_order_id="venue-test", client_order_id=CLIENT)
    assert result.status is (OrderStatus.CANCELED if status == "CANCELLED" else OrderStatus.OPEN)
    assert result.reject_reason == (None if status == "CANCELLED" else "cancel_pending")
    assert [c for c in transport.calls if c[0] == "POST"] == [
        ("POST", CANCEL, {"order_ids": ["venue-test"]})
    ]
    assert len(transport.calls) <= 5
