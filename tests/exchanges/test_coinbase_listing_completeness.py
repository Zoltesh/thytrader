"""Strict Coinbase listing regressions: no malformed page can claim complete coverage."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tests.exchanges.test_coinbase_open_orders import _ClientBase, _Response
from tests.operator_diagnostics.test_readiness_completeness import _order
from tests.operator_diagnostics.test_readiness_preflight import _deployment, _seed
from thytrader.exchanges import coinbase
from thytrader.exchanges.coinbase import CoinbaseAccount, CoinbasePaginationError
from thytrader.exchanges.read_errors import (
    ExchangeReadError,
    ExchangeReadFailureKind,
    ExchangeReadOperation,
)
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.operator.venue_reconciliation import build_venue_reconciliation_report
from thytrader.portfolio.service import PortfolioService


def _row(status: str = "OPEN", order_id: str = "venue-1") -> dict[str, Any]:
    """SDK JSON boundary fixture; dynamic values are narrowed by the adapter parser."""
    return {"order_id": order_id, "product_id": "BTC-USDC", "side": "BUY", "status": status}


class _Pages(_ClientBase):
    """Script distinct page payloads and retain read-only invocation evidence."""

    def __init__(self, pages: tuple[dict[str, Any], ...]) -> None:
        """Store external-boundary JSON fixtures; no actual SDK or network client."""
        self.pages = pages
        self.calls: list[dict[str, Any]] = []

    def list_orders(self, **kwargs: Any) -> _Response:
        """Return the next scripted page, with no write method available."""
        self.calls.append(kwargs)
        return _Response(self.pages[len(self.calls) - 1])


@pytest.mark.parametrize(
    "page",
    [
        {},
        {"orders": {}, "has_next": False},
        {"orders": []},
        {"orders": [], "has_next": "false"},
        {"orders": [], "has_next": 0},
        {"orders": [None], "has_next": False},
        {"orders": [{}], "has_next": False},
        {"orders": [_row(order_id=" ")], "has_next": False},
        {"orders": [{**_row(), "product_id": None}], "has_next": False},
        {"orders": [{**_row(), "product_id": "BTC-"}], "has_next": False},
        {"orders": [{**_row(), "side": "future-side"}], "has_next": False},
        {"orders": [{**_row(), "status": None}], "has_next": False},
        {"orders": [_row("UNKNOWN_ORDER_STATUS")], "has_next": False},
        {"orders": [_row("future-status")], "has_next": False},
        {"orders": [{**_row(), "client_order_id": 42}], "has_next": False},
        {"orders": [_row(), _row()], "has_next": False},
    ],
)
def test_malformed_orders_page_fails_closed(page: dict[str, Any]) -> None:
    """Missing fields, unidentified rows, unknown status and duplicate IDs are not dropped."""
    with pytest.raises(ExchangeReadError) as raised:
        asyncio.run(CoinbaseAccount(_Pages((page,))).list_open_orders())
    assert raised.value.failure.operation is ExchangeReadOperation.OPEN_ORDERS
    assert raised.value.failure.kind is ExchangeReadFailureKind.INVALID_RESPONSE


@pytest.mark.parametrize(
    "second",
    [
        {"orders": [_row()], "has_next": False},
        {"orders": [None], "has_next": False},
        {"orders": [_row("future-status", "venue-2")], "has_next": False},
    ],
)
def test_bad_later_page_never_returns_prior_valid_orders(second: dict[str, Any]) -> None:
    """Duplicates across pages and later parse failures invalidate the entire observation."""
    client = _Pages(({"orders": [_row()], "has_next": True, "cursor": "next"}, second))
    with pytest.raises(ExchangeReadError):
        asyncio.run(CoinbaseAccount(client).list_open_orders())
    assert len(client.calls) == 2


def test_cursor_cycle_and_page_limit_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-adjacent cursor cycles and unique-cursor page exhaustion cannot claim coverage."""
    cycle = _Pages(
        (
            {"orders": [_row(order_id="1")], "has_next": True, "cursor": "A"},
            {"orders": [_row(order_id="2")], "has_next": True, "cursor": "B"},
            {"orders": [_row(order_id="3")], "has_next": True, "cursor": "A"},
        )
    )
    with pytest.raises(CoinbasePaginationError, match="repeated cursor"):
        asyncio.run(CoinbaseAccount(cycle).list_open_orders())
    monkeypatch.setattr(coinbase, "_MAX_ORDER_PAGES", 2)
    with pytest.raises(CoinbasePaginationError, match="page limit"):
        asyncio.run(CoinbaseAccount(_Pages(tuple(cycle.pages[:2]))).list_open_orders())


def test_nonterminal_status_scope_and_terminal_filtering() -> None:
    """No status filter hides cancel/edit queues; only validated terminal states are omitted."""
    statuses = (
        "OPEN",
        "PENDING",
        "QUEUED",
        "CANCEL_QUEUED",
        "EDIT_QUEUED",
        "FILLED",
        "CANCELLED",
        "EXPIRED",
        "FAILED",
    )
    client = _Pages(
        (
            {
                "orders": [_row(status, str(index)) for index, status in enumerate(statuses)],
                "has_next": False,
            },
        )
    )
    observed = asyncio.run(CoinbaseAccount(client).list_open_orders())
    assert tuple(order.status for order in observed) == statuses[:5]
    assert client.calls[0]["product_type"] == "SPOT"
    assert all(
        key not in client.calls[0]
        for key in ("order_status", "start_date", "end_date", "retail_portfolio_id")
    )


def test_cancel_queued_managed_order_is_matched_not_orphan() -> None:
    """An unconfirmed venue cancellation is still working, not rejected or absent."""

    async def scenario() -> None:
        store = InMemoryExecutionStore()
        book = await _seed(store, _deployment())
        order = await _order(store, book, "BTC-USDC", "10", venue_id="cancel-queued")
        client = _Pages(
            (
                {
                    "orders": [
                        {
                            **_row("CANCEL_QUEUED", "cancel-queued"),
                            "client_order_id": order.client_order_id,
                        }
                    ],
                    "has_next": False,
                },
            )
        )
        report = await build_venue_reconciliation_report(
            portfolio=PortfolioService(CoinbaseAccount(client)),
            execution=store,
        )
        assert report.payload.orders_listing.scope == "spot_order_history_nonterminal"
        assert report.payload.orders_listing.status == "complete"
        assert report.payload.orders.matched == 1 and report.payload.orders.orphan == ()
        assert report.payload.orders.foreign == ()
        assert not any(
            finding.reason_code == "MANAGED_ORDER_NOT_AT_VENUE"
            for finding in report.payload.findings
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "page",
    [
        {},
        {"accounts": []},
        {"accounts": [], "has_next": "false"},
        {"accounts": [None], "has_next": False},
        {"accounts": [{}], "has_next": False},
        {
            "accounts": [
                {"currency": "BTC", "available_balance": {"value": "NaN"}, "hold": {"value": "0"}}
            ],
            "has_next": False,
        },
        {"accounts": [{"currency": "BTC", "available_balance": {"value": "1"}}], "has_next": False},
    ],
)
def test_malformed_balance_listing_cannot_claim_empty_complete_venue(page: dict[str, Any]) -> None:
    """Malformed account rows or continuation flags cannot become a complete empty balance."""

    class _Accounts(_Pages):
        """Return one synthetic account listing, retaining the SDK-shaped client surface."""

        def get_accounts(self, *, limit: int, cursor: str | None = None) -> _Response:
            """Expose boundary JSON for fail-closed parser tests."""
            del limit, cursor
            return _Response(page)

    with pytest.raises(ExchangeReadError) as raised:
        asyncio.run(CoinbaseAccount(_Accounts(())).list_balances())
    assert raised.value.failure.operation is ExchangeReadOperation.BALANCES
    assert raised.value.failure.kind is ExchangeReadFailureKind.INVALID_RESPONSE
