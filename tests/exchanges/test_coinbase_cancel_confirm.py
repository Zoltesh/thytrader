"""Coinbase cancel confirmation: batch_cancel parsing, CANCEL_QUEUED, bounded GET re-checks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from thytrader.exchanges.coinbase_broker import CoinbaseRestBroker
from thytrader.execution.broker import CANCEL_PENDING_REASON
from thytrader.execution.models import OrderStatus

if TYPE_CHECKING:
    from collections.abc import Mapping

_CANCEL_PATH = "/api/v3/brokerage/orders/batch_cancel"
_ORDER_PATH = "/api/v3/brokerage/orders/historical/child-1"


class _Transport:
    """Queue one batch_cancel body and a list of GET-order statuses for one order."""

    def __init__(self, *, ack: dict[str, Any], statuses: list[str]) -> None:
        """Bind the scripted responses."""
        self._ack = ack
        self._statuses = statuses
        self.calls: list[str] = []

    def get(self, path: str, params: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Return the next GET-order body (the last one repeats)."""
        del params
        assert path == _ORDER_PATH
        self.calls.append("GET")
        status = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        return {"order": {"order_id": "child-1", "status": status, "filled_size": "0"}}

    def post(self, path: str, data: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Return the batch_cancel acknowledgement."""
        assert path == _CANCEL_PATH
        assert data == {"order_ids": ["child-1"]}
        self.calls.append("POST")
        return self._ack


def _ack(*, success: bool, failure_reason: str) -> dict[str, Any]:
    """Build one documented batch_cancel response."""
    return {
        "results": [{"success": success, "failure_reason": failure_reason, "order_id": "child-1"}]
    }


_ACCEPTED = _ack(success=True, failure_reason="UNKNOWN_CANCEL_FAILURE_REASON")


async def _cancel(transport: _Transport) -> tuple[OrderStatus, str | None]:
    """Cancel the child with zero re-check delay and return status and reason."""
    broker = CoinbaseRestBroker(transport, cancel_confirm_delay_seconds=0)
    result = await broker.cancel_order(venue_order_id="child-1", client_order_id="c")
    return result.status, result.reject_reason


@pytest.mark.anyio
async def test_queued_cancel_is_rechecked_until_confirmed() -> None:
    """An accepted cancel that first GETs as CANCEL_QUEUED is re-checked, not failed."""
    transport = _Transport(ack=_ACCEPTED, statuses=["CANCEL_QUEUED", "CANCELLED"])
    status, _reason = await _cancel(transport)
    assert status is OrderStatus.CANCELED
    assert transport.calls == ["POST", "GET", "GET"]


@pytest.mark.anyio
async def test_cancel_still_queued_after_bounded_rechecks_is_pending() -> None:
    """Still on the book after three GETs: active, marked pending, one POST only."""
    transport = _Transport(ack=_ACCEPTED, statuses=["CANCEL_QUEUED"])
    status, reason = await _cancel(transport)
    assert status is OrderStatus.OPEN
    assert reason == CANCEL_PENDING_REASON
    assert transport.calls == ["POST", "GET", "GET", "GET"]


@pytest.mark.anyio
async def test_duplicate_cancel_is_pending_and_other_refusals_name_the_reason() -> None:
    """DUPLICATE_CANCEL_REQUEST means a cancel is in flight; other refusals are reported."""
    duplicate = _Transport(
        ack=_ack(success=False, failure_reason="DUPLICATE_CANCEL_REQUEST"), statuses=["OPEN"]
    )
    assert await _cancel(duplicate) == (OrderStatus.OPEN, CANCEL_PENDING_REASON)
    refused = _Transport(
        ack=_ack(success=False, failure_reason="INVALID_CANCEL_REQUEST"), statuses=["OPEN"]
    )
    assert await _cancel(refused) == (
        OrderStatus.OPEN,
        "cancel_failed:INVALID_CANCEL_REQUEST",
    )


@pytest.mark.anyio
async def test_refused_cancel_on_a_filled_order_reports_the_fill() -> None:
    """GET order is the truth: a cancel refused because the order filled returns FILLED."""
    transport = _Transport(
        ack=_ack(success=False, failure_reason="UNKNOWN_CANCEL_ORDER"), statuses=["FILLED"]
    )
    status, _reason = await _cancel(transport)
    assert status is OrderStatus.FILLED
    assert transport.calls == ["POST", "GET"]


@pytest.mark.anyio
async def test_unrecognized_cancel_body_still_trusts_get_order() -> None:
    """A malformed batch_cancel body does not fail the cancel when GET shows it cancelled."""
    transport = _Transport(ack={}, statuses=["CANCELLED"])
    status, _reason = await _cancel(transport)
    assert status is OrderStatus.CANCELED
