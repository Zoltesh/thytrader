"""Definite vs ambiguous Coinbase create-order outcomes and client-id recovery lookup."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from tests.exchanges.test_coinbase_broker import FakeTransport
from thytrader.exchanges.coinbase_broker import CoinbaseRestBroker
from thytrader.exchanges.rest_transport import CoinbaseHttpStatusError, RestClientTransport
from thytrader.execution.broker import BrokerError, ClientOrderLookup, SubmitResult
from thytrader.trading.models import OrderKind, OrderSide, OrderStatus

if TYPE_CHECKING:
    from collections.abc import Mapping

_ORDERS = "/api/v3/brokerage/orders"
_BATCH = "/api/v3/brokerage/orders/historical/batch"


class _Response:
    """Minimal requests.Response stand-in carrying a status and JSON text."""

    def __init__(self, status_code: int, text: str) -> None:
        """Bind status and body text."""
        self.status_code = status_code
        self.text = text


class _SdkHttpError(OSError):
    """Stand-in for ``requests.HTTPError`` (an OSError with ``response``)."""

    def __init__(self, status_code: int, text: str) -> None:
        """Attach a fake response like the SDK's handle_exception does."""
        super().__init__(f"{status_code} Client Error")
        self.response = _Response(status_code, text)


class _RaisingTransport:
    """POST raises a prepared error; GET is never expected."""

    def __init__(self, error: Exception) -> None:
        """Bind the error each POST raises."""
        self.error = error
        self.posts = 0

    def get(self, path: str, params: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Fail loudly: a rejected or ambiguous create must not GET."""
        raise AssertionError(f"unexpected GET {path} {params}")

    def post(self, path: str, data: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Raise the prepared transport error."""
        del path, data
        self.posts += 1
        raise self.error


async def _place(broker: CoinbaseRestBroker) -> SubmitResult:
    """Submit one marketable buy."""
    return await broker.place_order(
        client_order_id="dep:entry:20260101T0000:abc",
        product_id="BTC-USDC",
        side=OrderSide.BUY,
        kind=OrderKind.MARKETABLE,
        quantity=Decimal("0.01"),
        price=None,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
async def test_definite_http_rejection_is_terminal_rejected(status_code: int) -> None:
    """A 4xx that proves no order exists maps to REJECTED with a redacted reason."""
    transport = _RaisingTransport(CoinbaseHttpStatusError(status_code, "INVALID_ARGUMENT"))
    result = await _place(CoinbaseRestBroker(transport))
    assert result.status is OrderStatus.REJECTED
    assert result.reject_reason == f"coinbase_http_{status_code}:INVALID_ARGUMENT"
    assert transport.posts == 1


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [408, 409, 429, 500, 502, 503, 504])
async def test_ambiguous_http_status_raises_broker_error(status_code: int) -> None:
    """Timeouts, conflicts, throttles, and 5xx stay ambiguous (caller records UNKNOWN)."""
    transport = _RaisingTransport(CoinbaseHttpStatusError(status_code, None))
    with pytest.raises(BrokerError):
        await _place(CoinbaseRestBroker(transport))
    assert transport.posts == 1


@pytest.mark.anyio
@pytest.mark.parametrize("error", [TimeoutError("read timed out"), ConnectionResetError()])
async def test_transport_failures_stay_ambiguous(error: Exception) -> None:
    """Network failures are never proof of rejection."""
    with pytest.raises(BrokerError):
        await _place(CoinbaseRestBroker(_RaisingTransport(error)))


class _SdkClient:
    """Duck-typed SDK client whose POST raises an SDK-style HTTP error."""

    def __init__(self, error: OSError) -> None:
        """Bind the error."""
        self.error = error

    def post(self, path: str, data: dict[str, object]) -> object:
        """Raise like RESTClient after handle_exception."""
        del path, data
        raise self.error

    def get(self, path: str, params: dict[str, object]) -> object:
        """Raise like RESTClient after handle_exception."""
        del path, params
        raise self.error


def test_transport_maps_sdk_http_error_to_redacted_status_error() -> None:
    """The SDK HTTPError becomes CoinbaseHttpStatusError without raw body text."""
    body = '{"error":"PERMISSION_DENIED","message":"organizations/abc/apiKeys/xyz"}'
    transport = RestClientTransport(_SdkClient(_SdkHttpError(403, body)))
    with pytest.raises(CoinbaseHttpStatusError) as raised:
        transport.post(_ORDERS, {})
    assert raised.value.status_code == 403
    assert raised.value.error_code == "PERMISSION_DENIED"
    assert "apiKeys" not in str(raised.value)
    assert isinstance(raised.value, OSError)


def test_transport_ignores_unshaped_error_tokens() -> None:
    """Free-text error fields are not echoed as error codes."""
    transport = RestClientTransport(_SdkClient(_SdkHttpError(400, '{"error":"has spaces!"}')))
    with pytest.raises(CoinbaseHttpStatusError) as raised:
        transport.get(_BATCH, {})
    assert raised.value.error_code is None


def test_transport_reraises_non_http_os_errors_unchanged() -> None:
    """Connection errors without a response keep their original type."""
    transport = RestClientTransport(_SdkClient(ConnectionResetError()))
    with pytest.raises(ConnectionResetError):
        transport.post(_ORDERS, {})


def test_coinbase_broker_advertises_client_order_lookup() -> None:
    """Reconcile discovers the recovery capability through the runtime protocol."""
    assert isinstance(CoinbaseRestBroker(FakeTransport(gets={})), ClientOrderLookup)


@pytest.mark.anyio
async def test_client_lookup_is_bounded_to_product_and_submit_window() -> None:
    """Lookup filters by product and a start_date just before submit, then GETs the order."""
    historical = "/api/v3/brokerage/orders/historical/venue-7"
    transport = FakeTransport(
        gets={
            _BATCH: [
                {
                    "orders": [{"order_id": "other", "client_order_id": "nope"}],
                    "has_next": True,
                    "cursor": "c2",
                },
                {
                    "orders": [{"order_id": "venue-7", "client_order_id": "cid-7"}],
                    "has_next": False,
                },
            ],
            historical: [{"order": {"order_id": "venue-7", "status": "OPEN"}}],
        }
    )
    found = await CoinbaseRestBroker(transport).find_order_by_client_id(
        client_order_id="cid-7",
        product_id="BTC-USDC",
        submitted_at=datetime(2026, 9, 29, 12, 0, tzinfo=UTC),
    )
    assert found is not None
    assert found.venue_order_id == "venue-7"
    assert found.status is OrderStatus.OPEN
    first = transport.calls[0][2]
    assert first["product_ids"] == ["BTC-USDC"]
    assert first["start_date"] == "2026-09-29T11:55:00Z"
    assert "order_placement_source" not in first
    assert transport.calls[1][2]["cursor"] == "c2"


@pytest.mark.anyio
async def test_client_lookup_returns_none_only_after_a_complete_scan() -> None:
    """A finished scan with no match returns None; a broken page raises."""
    transport = FakeTransport(gets={_BATCH: [{"orders": [], "has_next": False}]})
    missing = await CoinbaseRestBroker(transport).find_order_by_client_id(
        client_order_id="cid-x",
        product_id="BTC-USDC",
        submitted_at=datetime(2026, 9, 29, tzinfo=UTC),
    )
    assert missing is None
    broken = FakeTransport(gets={_BATCH: [{"orders": [], "has_next": True}]})
    with pytest.raises(BrokerError):
        await CoinbaseRestBroker(broken).find_order_by_client_id(
            client_order_id="cid-x",
            product_id="BTC-USDC",
            submitted_at=datetime(2026, 9, 29, tzinfo=UTC),
        )
