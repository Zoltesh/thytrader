"""Cancellation acknowledgement is separate from terminal order evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tests.execution.test_futures_reduction import open_live_book
from tests.execution_worker.futures_safety_support import cancels, protect
from tests.execution_worker.test_futures_cancel_read_failure import ConfirmationFailureVenue
from thytrader.execution.broker import CANCEL_PENDING_REASON, BrokerError
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import OrderStatus

if TYPE_CHECKING:
    from collections.abc import Mapping


class AckVenue(ConfirmationFailureVenue):
    """Supply synthetic untrusted acknowledgement JSON after the cancel is sent."""

    def __init__(self, shape: str) -> None:
        """Select a malformed or definite-negative result."""
        super().__init__("readable")
        self.shape = shape

    def post(self, path: str, data: Mapping[str, object] | None = None) -> dict[str, Any]:
        """Keep the transport real except for the response shape under test."""
        result = super().post(path, data)
        if not path.endswith("/batch_cancel"):
            return result
        row = result["results"][0]
        if self.shape == "empty":
            return {}
        if self.shape == "missing_id":
            row.pop("order_id")
        elif self.shape == "wrong_id":
            row["order_id"] = "unrelated-order"
        elif self.shape == "string_success":
            row["success"] = "true"
        elif self.shape == "duplicate":
            result["results"].append(dict(row))
        else:
            row["success"] = False
            row["failure_reason"] = self.shape
        return result


@pytest.mark.anyio
@pytest.mark.parametrize(
    "shape", ["empty", "missing_id", "wrong_id", "string_success", "duplicate"]
)
async def test_malformed_cancel_ack_is_neither_acceptance_nor_rejection(shape: str) -> None:
    """Unknown JSON must not be promoted into pending acceptance or definite failure."""
    venue = AckVenue(shape)
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    _, order = await protect(store, venue, snapshot)
    assert order.venue_order_id is not None
    venue.cancel_confirmed = False
    with pytest.raises(BrokerError, match="acknowledgement"):
        await venue.broker().cancel_order(
            venue_order_id=order.venue_order_id, client_order_id=order.client_order_id
        )
    assert cancels(venue) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["UNKNOWN_CANCEL_ORDER", "DUPLICATE_CANCEL_REQUEST"])
async def test_definite_cancel_failure_and_duplicate_remain_distinct(failure: str) -> None:
    """Only explicit duplicate acknowledgement shares accepted-pending semantics."""
    venue = AckVenue(failure)
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    _, order = await protect(store, venue, snapshot)
    assert order.venue_order_id is not None
    venue.cancel_confirmed = False
    result = await venue.broker().cancel_order(
        venue_order_id=order.venue_order_id, client_order_id=order.client_order_id
    )
    assert result.status is OrderStatus.OPEN
    assert result.reject_reason == (
        CANCEL_PENDING_REASON
        if failure == "DUPLICATE_CANCEL_REQUEST"
        else f"cancel_failed:{failure}"
    )
