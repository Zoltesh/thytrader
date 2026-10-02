"""Behavioral tests for the Coinbase Advanced Trade account adapter."""

import asyncio
from decimal import Decimal
import logging
from typing import Any

import pytest
from requests import HTTPError, Response

from thytrader.exchanges.coinbase import CoinbaseAccount, CoinbasePaginationError


class StubCoinbaseClient:
    """Small SDK-shaped client returning validated boundary fixtures."""

    def __init__(self) -> None:
        """Track pagination calls for verification."""
        self.account_cursors: list[str | None] = []

    def get_accounts(self, *, limit: int, cursor: str | None = None) -> Any:
        """Return two account pages including a zero balance."""
        assert limit == 250
        self.account_cursors.append(cursor)
        if cursor is None:
            return StubResponse(
                {
                    "accounts": [
                        {
                            "name": "BTC Wallet",
                            "currency": "BTC",
                            "available_balance": {"value": "0.5", "currency": "BTC"},
                            "hold": {"value": "0.1", "currency": "BTC"},
                            "active": True,
                        },
                        {
                            "name": "Empty Wallet",
                            "currency": "ETH",
                            "available_balance": {"value": "0", "currency": "ETH"},
                            "hold": {"value": "0", "currency": "ETH"},
                            "active": True,
                        },
                    ],
                    "has_next": True,
                    "cursor": "next-page",
                }
            )
        return StubResponse(
            {
                "accounts": [
                    {
                        "name": "USD Wallet",
                        "currency": "USD",
                        "available_balance": {"value": "25.50", "currency": "USD"},
                        "hold": {"value": "0", "currency": "USD"},
                        "active": True,
                    }
                ],
                "has_next": False,
                "cursor": "",
            }
        )

    def get_api_key_permissions(self) -> Any:
        """Return every permission to prove none are rejected."""
        return StubResponse({"can_view": True, "can_trade": True, "can_transfer": True})

    def get_product(self, product_id: str) -> Any:
        """Return a direct USD price or a not-found-like SDK failure."""
        if product_id == "BTC-USD":
            return StubResponse({"product_id": product_id, "price": "60000.25"})
        response = Response()
        response.status_code = 404
        raise HTTPError("product unavailable", response=response)

    def get_transaction_summary(self, **kwargs: Any) -> Any:
        """Return a stubbed transaction summary."""
        del kwargs
        return StubResponse(
            {
                "total_volume": 0,
                "fee_tier": {
                    "pricing_tier": "Tier 1",
                    "taker_fee_rate": "0.006",
                    "maker_fee_rate": "0.004",
                },
            }
        )


class StubResponse:
    """Coinbase SDK response exposing its documented dictionary conversion."""

    def __init__(self, payload: dict[str, Any]) -> None:
        """Store one synthetic SDK payload."""
        self._payload = payload

    def to_dict(self) -> dict[str, Any]:
        """Return a defensive payload copy."""
        return dict(self._payload)


def test_coinbase_adapter_paginates_balances_and_ignores_empty_accounts() -> None:
    """Account pagination should retain exact non-empty balances only."""
    client = StubCoinbaseClient()
    adapter = CoinbaseAccount(client)

    balances = asyncio.run(adapter.list_balances())

    assert client.account_cursors == [None, "next-page"]
    assert tuple(balance.currency for balance in balances) == ("BTC", "USD")
    assert balances[0].available == Decimal("0.5")
    assert balances[0].hold == Decimal("0.1")


def test_coinbase_adapter_reports_all_permissions_without_gating() -> None:
    """View, trade, and transfer permissions should all remain accepted and visible."""
    permissions = asyncio.run(CoinbaseAccount(StubCoinbaseClient()).get_permissions())

    assert permissions == ("view", "trade", "transfer")


def test_coinbase_adapter_returns_direct_usd_price_or_none() -> None:
    """Unavailable direct USD markets should remain explicitly unvalued."""
    adapter = CoinbaseAccount(StubCoinbaseClient(), unsupported_products=set())

    assert asyncio.run(adapter.get_usd_price("BTC")) == Decimal("60000.25")
    assert asyncio.run(adapter.get_usd_price("OBSCURE")) is None


def test_unsupported_usd_products_are_asked_once_and_logged_once_at_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A dust asset's 404 is cached per process: one INFO line, no SDK ERROR (ADR 0094)."""

    class LoggingClient(StubCoinbaseClient):
        """Log like the official SDK does before raising its 404."""

        def __init__(self) -> None:
            """Count product lookups."""
            super().__init__()
            self.product_calls: list[str] = []

        def get_product(self, product_id: str) -> Any:
            """Emit the SDK's ERROR line, then raise the not-found failure."""
            self.product_calls.append(product_id)
            if product_id != "BTC-USD":
                logging.getLogger("coinbase.RESTClient").error(
                    'HTTP Error: 404 Client Error: Not Found {"error":"NOT_FOUND"}'
                )
            return super().get_product(product_id)

    client = LoggingClient()
    cache: set[str] = set()
    adapter = CoinbaseAccount(client, unsupported_products=cache)
    with caplog.at_level(logging.INFO):
        assert asyncio.run(adapter.get_usd_price("IOTX")) is None
        assert asyncio.run(adapter.get_usd_price("IOTX")) is None
        assert (
            asyncio.run(CoinbaseAccount(client, unsupported_products=cache).get_usd_price("IOTX"))
            is None
        )
    assert client.product_calls == ["IOTX-USD"]
    assert cache == {"IOTX-USD"}
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
    infos = [record for record in caplog.records if "IOTX-USD" in record.getMessage()]
    assert len(infos) == 1
    assert infos[0].levelno == logging.INFO


def test_unexpected_sdk_errors_are_still_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Only the expected product 404 is muted; other SDK errors keep their ERROR line."""
    CoinbaseAccount(StubCoinbaseClient(), unsupported_products=set())
    with caplog.at_level(logging.ERROR):
        logging.getLogger("coinbase.RESTClient").error("HTTP Error: 404 Client Error: orders")
        logging.getLogger("coinbase.RESTClient").error("HTTP Error: 503 Server Error")
    assert len([record for record in caplog.records if record.levelno == logging.ERROR]) == 2


def test_coinbase_adapter_propagates_non_not_found_price_failures() -> None:
    """Authentication and service failures must not silently undervalue assets."""

    class UnavailableClient(StubCoinbaseClient):
        """Fail product requests with a transient Coinbase response."""

        def get_product(self, product_id: str) -> Any:
            """Raise a service-unavailable response for every product."""
            response = Response()
            response.status_code = 503
            raise HTTPError(f"Coinbase unavailable for {product_id}", response=response)

    with pytest.raises(HTTPError, match="Coinbase unavailable"):
        asyncio.run(CoinbaseAccount(UnavailableClient()).get_usd_price("BTC"))


def test_coinbase_adapter_rejects_a_repeated_pagination_cursor() -> None:
    """A malformed repeated cursor must not return an incomplete portfolio."""

    class RepeatedCursorClient(StubCoinbaseClient):
        """Return the same cursor forever to emulate a malformed upstream page."""

        def get_accounts(self, *, limit: int, cursor: str | None = None) -> Any:
            """Return an empty page with a repeated next cursor."""
            assert limit == 250
            self.account_cursors.append(cursor)
            return StubResponse({"accounts": [], "has_next": True, "cursor": "repeat"})

    client = RepeatedCursorClient()

    with pytest.raises(CoinbasePaginationError, match="repeated cursor"):
        asyncio.run(CoinbaseAccount(client).list_balances())

    assert client.account_cursors == [None, "repeat"]


def test_coinbase_adapter_rejects_a_missing_pagination_cursor() -> None:
    """A continuation flag without a cursor must not silently truncate balances."""

    class MissingCursorClient(StubCoinbaseClient):
        """Return malformed continuation metadata after a valid account page."""

        def get_accounts(self, *, limit: int, cursor: str | None = None) -> Any:
            """Return an invalid next-page response."""
            assert limit == 250
            self.account_cursors.append(cursor)
            return StubResponse({"accounts": [], "has_next": True})

    with pytest.raises(CoinbasePaginationError, match="missing cursor"):
        asyncio.run(CoinbaseAccount(MissingCursorClient()).list_balances())


def test_coinbase_adapter_rejects_an_unbounded_unique_cursor_stream() -> None:
    """A malformed stream of unique cursors must stop at the hard page cap."""

    class EndlessCursorClient(StubCoinbaseClient):
        """Return one unique continuation cursor for every request."""

        def get_accounts(self, *, limit: int, cursor: str | None = None) -> Any:
            """Produce a continuation stream that cannot terminate naturally."""
            assert limit == 250
            self.account_cursors.append(cursor)
            return StubResponse(
                {"accounts": [], "has_next": True, "cursor": f"cursor-{len(self.account_cursors)}"}
            )

    client = EndlessCursorClient()

    with pytest.raises(CoinbasePaginationError, match="page limit"):
        asyncio.run(CoinbaseAccount(client).list_balances())

    assert len(client.account_cursors) == 100
