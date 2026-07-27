"""Behavioral tests for the Coinbase Advanced Trade account adapter."""

import asyncio
from decimal import Decimal
from typing import Any

import pytest
from requests import HTTPError, Response

from thytrader.exchanges.coinbase import CoinbaseAccount


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
    adapter = CoinbaseAccount(StubCoinbaseClient())

    assert asyncio.run(adapter.get_usd_price("BTC")) == Decimal("60000.25")
    assert asyncio.run(adapter.get_usd_price("OBSCURE")) is None


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


def test_coinbase_adapter_stops_on_a_repeated_pagination_cursor() -> None:
    """A malformed repeated cursor must not create an infinite account loop."""

    class RepeatedCursorClient(StubCoinbaseClient):
        """Return the same cursor forever to emulate a malformed upstream page."""

        def get_accounts(self, *, limit: int, cursor: str | None = None) -> Any:
            """Return an empty page with a repeated next cursor."""
            assert limit == 250
            self.account_cursors.append(cursor)
            return StubResponse({"accounts": [], "has_next": True, "cursor": "repeat"})

    client = RepeatedCursorClient()

    balances = asyncio.run(CoinbaseAccount(client).list_balances())

    assert balances == ()
    assert client.account_cursors == [None, "repeat"]
