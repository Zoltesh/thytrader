"""Read-only open-order listing pagination for venue reconciliation (ADR 0114)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from requests import HTTPError, Response, Timeout

from thytrader.exchanges.coinbase import CoinbaseAccount, CoinbasePaginationError
from thytrader.exchanges.models import ExchangeOpenOrder
from thytrader.exchanges.read_errors import (
    ExchangeReadError,
    ExchangeReadFailureKind,
    ExchangeReadOperation,
)


class _Response:
    """SDK-shaped response for one synthetic orders page."""

    def __init__(self, payload: dict[str, Any]) -> None:
        """Store the page payload."""
        self._payload = payload

    def to_dict(self) -> dict[str, Any]:
        """Return the page as a mapping."""
        return dict(self._payload)


class _ClientBase:
    """SDK-shaped client base so paging fakes only vary ``list_orders``."""

    def get_accounts(self, *, limit: int, cursor: str | None = None) -> _Response:
        """Return an empty balance page; unused by open-order tests."""
        del limit, cursor
        return _Response({"accounts": [], "has_next": False})

    def get_api_key_permissions(self) -> _Response:
        """Return view and trade permissions; unused by open-order tests."""
        return _Response({"can_view": True, "can_trade": True})

    def get_product(self, product_id: str) -> _Response:
        """Return a not-found-like product failure; unused by open-order tests."""
        del product_id
        response = Response()
        response.status_code = 404
        raise HTTPError("product unavailable", response=response)

    def get_transaction_summary(self, **kwargs: Any) -> _Response:
        """Return a tier-one fee summary; unused by open-order tests."""
        del kwargs
        return _Response(
            {
                "total_volume": 0,
                "fee_tier": {
                    "pricing_tier": "Tier 1",
                    "taker_fee_rate": "0.006",
                    "maker_fee_rate": "0.004",
                },
            }
        )


class _PagingClient(_ClientBase):
    """Two complete spot-history pages, then stop."""

    def __init__(self) -> None:
        """Record cursors so tests can prove the second page was requested."""
        self.cursors: list[str | None] = []

    def list_orders(self, **kwargs: Any) -> _Response:
        """Return page one, then page two, and never a write."""
        assert "order_status" not in kwargs
        assert kwargs["product_type"] == "SPOT"
        cursor = kwargs.get("cursor")
        self.cursors.append(cursor if isinstance(cursor, str) else None)
        if cursor is None:
            return _Response(
                {
                    "orders": [
                        {
                            "order_id": "venue-1",
                            "product_id": "BTC-USDC",
                            "side": "BUY",
                            "status": "OPEN",
                            "client_order_id": "client-1",
                        },
                    ],
                    "has_next": True,
                    "cursor": "page-2",
                }
            )
        return _Response(
            {
                "orders": [
                    {
                        "order_id": "venue-2",
                        "product_id": "ETH-USDC",
                        "side": "SELL",
                        "status": "OPEN",
                    }
                ],
                "has_next": False,
            }
        )


class _RepeatedCursorClient(_ClientBase):
    """Declare a next page and then repeat the same cursor."""

    def list_orders(self, **kwargs: Any) -> _Response:
        """Always point at the same cursor."""
        del kwargs
        return _Response(
            {
                "orders": [],
                "has_next": True,
                "cursor": "again",
            }
        )


class _MissingCursorClient(_ClientBase):
    """Declare a next page without a cursor."""

    def list_orders(self, **kwargs: Any) -> _Response:
        """Omit the cursor."""
        del kwargs
        return _Response(
            {
                "orders": [
                    {
                        "order_id": "venue-1",
                        "product_id": "BTC-USDC",
                        "side": "BUY",
                        "status": "OPEN",
                    }
                ],
                "has_next": True,
                "cursor": "",
            }
        )


def test_open_orders_page_until_complete() -> None:
    """A complete listing retains both pages without silently dropping malformed rows."""
    client = _PagingClient()
    orders = asyncio.run(CoinbaseAccount(client).list_open_orders())
    assert client.cursors == [None, "page-2"]
    assert orders == (
        ExchangeOpenOrder(
            venue_order_id="venue-1",
            product_id="BTC-USDC",
            side="buy",
            status="OPEN",
            client_order_id="client-1",
        ),
        ExchangeOpenOrder(
            venue_order_id="venue-2",
            product_id="ETH-USDC",
            side="sell",
            status="OPEN",
            client_order_id=None,
        ),
    )


def test_repeated_open_order_cursor_fails_closed() -> None:
    """A repeated cursor is not treated as a complete venue listing."""
    with pytest.raises(CoinbasePaginationError, match="repeated cursor"):
        asyncio.run(CoinbaseAccount(_RepeatedCursorClient()).list_open_orders())


def test_missing_open_order_cursor_fails_closed() -> None:
    """A declared next page without a cursor does not return the first page as complete."""
    with pytest.raises(CoinbasePaginationError, match="missing cursor"):
        asyncio.run(CoinbaseAccount(_MissingCursorClient()).list_open_orders())


def test_open_order_transport_failure_is_typed_and_not_partial() -> None:
    """A failed page raises typed evidence instead of a partial order tuple."""

    class _Failing(_ClientBase):
        def list_orders(self, **kwargs: Any) -> _Response:
            del kwargs
            raise Timeout

    with pytest.raises(ExchangeReadError) as raised:
        asyncio.run(CoinbaseAccount(_Failing()).list_open_orders())
    assert raised.value.failure.operation is ExchangeReadOperation.OPEN_ORDERS
    assert raised.value.failure.kind is ExchangeReadFailureKind.TIMEOUT


class _FuturesOrdersClient(_ClientBase):
    """One futures order-history page; spot ids are refused on a futures listing."""

    def __init__(self, product_id: str = "BIP-20DEC30-CDE") -> None:
        """Choose the listed product id."""
        self.product_id = product_id
        self.calls: list[dict[str, Any]] = []

    def list_orders(self, **kwargs: Any) -> _Response:
        """Return one page with an open and a filled futures order."""
        self.calls.append(kwargs)
        rows = [
            {"order_id": "f-1", "product_id": self.product_id, "side": "SELL", "status": "OPEN"},
            {"order_id": "f-2", "product_id": self.product_id, "side": "BUY", "status": "FILLED"},
        ]
        return _Response({"orders": rows, "has_next": False})


def test_futures_open_orders_are_listed_read_only_with_futures_ids() -> None:
    """The futures listing pages FUTURE history and keeps nonterminal CDE orders (ADR 0127)."""
    client = _FuturesOrdersClient()
    orders = asyncio.run(CoinbaseAccount(client).list_futures_open_orders())
    assert [(o.venue_order_id, o.product_id, o.side) for o in orders] == [
        ("f-1", "BIP-20DEC30-CDE", "sell")
    ]
    assert client.calls[0]["product_type"] == "FUTURE"


def test_a_spot_id_on_the_futures_listing_fails_closed() -> None:
    """A row whose id is not a futures id invalidates the futures listing."""
    with pytest.raises(ExchangeReadError) as caught:
        asyncio.run(CoinbaseAccount(_FuturesOrdersClient("BTC-USD")).list_futures_open_orders())
    assert caught.value.failure.operation is ExchangeReadOperation.FUTURES_OPEN_ORDERS
