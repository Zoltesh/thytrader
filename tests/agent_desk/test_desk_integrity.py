"""A desk's pool, journal and book values stay true through losses, refusals and lagging fills."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from tests.agent_desk.test_service import _desk, _Execution, _open, _RefusingExecution, _wake
from thytrader.agent_desk.models import Desk, WakeOutcome
from thytrader.agent_desk.ports import DeskError, save_desk
from thytrader.agent_desk.service import briefing
from thytrader.agent_desk.valuation import book_value
from thytrader.trading.models import (
    DeploymentStatus,
    OrderSide,
    OrderStatus,
    RuntimePhase,
    with_runtime,
)

if TYPE_CHECKING:
    from thytrader.agent_desk.store import InMemoryDeskStore
    from thytrader.execution.discretionary_request import DiscretionaryOrderRequest
    from thytrader.trading.models import DeploymentSnapshot

pytestmark = pytest.mark.anyio


async def _close_at_a_loss(execution: _Execution, deployment_id: str, *, exit_fee: Decimal) -> None:
    """Sell the whole entry back at the entry price with ``exit_fee``, then stop the book flat."""
    book = await execution.snapshot(deployment_id)
    entry_fill = book.fills[0]
    entry_order = next(order for order in book.orders if order.id == entry_fill.order_id)
    exit_order = replace(
        entry_order, id=uuid4(), side=OrderSide.SELL, client_order_id="exit", intent_id=uuid4()
    )
    await execution.store.save_order(exit_order)
    await execution.store.save_fill(
        replace(entry_fill, id=uuid4(), order_id=exit_order.id, venue_fill_id="exit", fee=exit_fee)
    )
    await execution.store.save_deployment(
        with_runtime(
            book.deployment,
            updated_at=datetime.now(UTC),
            status=DeploymentStatus.STOPPED,
            phase=RuntimePhase.FLAT,
        )
    )
    for order in book.orders:
        if order.status in {OrderStatus.OPEN, OrderStatus.PENDING}:
            await execution.store.save_order(replace(order, status=OrderStatus.CANCELED))
    await execution.store.save_position(None, deployment_id=book.deployment.id)


async def _current(store: InMemoryDeskStore, desk_id: str) -> Desk:
    desk = await store.get_desk(desk_id)
    assert desk is not None
    return desk


async def test_a_loss_beyond_the_pool_settles_to_a_readable_desk_that_funds_nothing() -> None:
    """Settling a book worth less than zero leaves negative cash that still validates on read."""
    store, execution, desk_id = await _desk()
    opened = await _wake(store, execution, desk_id, _open(fraction="0.1"))
    assert opened.outcome is WakeOutcome.APPLIED, opened.detail
    await _close_at_a_loss(execution, opened.deployment_id or "", exit_fee=Decimal(20000))
    await briefing(desk_id, store=store, execution=execution, market_data=execution.market_data)
    desk = await _current(store, desk_id)
    assert desk.cash < 0
    # What PostgreSQL stores is read back through validation; it must not brick every desk.
    assert Desk.model_validate_json(desk.model_dump_json()) == desk
    refused = await _wake(store, execution, desk_id, _open(product="ETH-USD", fraction="0.1"))
    assert refused.outcome is WakeOutcome.REFUSED
    assert (await _current(store, desk_id)).cash == desk.cash


async def test_a_desk_write_that_fails_validation_is_refused_before_it_is_stored() -> None:
    """A model copy skips validation, so the revisioned save checks the row it writes."""
    store, _, desk_id = await _desk()
    desk = await _current(store, desk_id)
    with pytest.raises(DeskError, match="DESK_INVALID"):
        await save_desk(desk.model_copy(update={"cadence_minutes": 1}), store=store, previous=desk)
    assert await _current(store, desk_id) == desk


async def test_a_refused_open_journals_only_its_refusal() -> None:
    """The reservation is refunded silently: no "interrupted open" row follows the refusal."""
    store, _, desk_id = await _desk()
    wake = await _wake(store, _RefusingExecution(), desk_id, _open())
    assert wake.outcome_code == "PAPER_FEES_UNAVAILABLE"
    assert [row.outcome_code for row in await store.list_wakes(desk_id, limit=10)] == [
        "PAPER_FEES_UNAVAILABLE"
    ]
    assert (await _current(store, desk_id)).cash == Decimal(10000)


async def test_a_filled_order_without_its_fills_has_no_known_value() -> None:
    """An executed quantity not yet imported as fills could move cash: the value is unknown."""
    store, execution, desk_id = await _desk()
    opened = await _wake(store, execution, desk_id, _open(fraction="0.1"))
    book = next(iter(await store.list_books(desk_id)))
    snapshot = await execution.snapshot(book.deployment_id)
    entry = next(order for order in snapshot.orders if order.id == snapshot.fills[0].order_id)
    sold = replace(
        entry,
        id=uuid4(),
        side=OrderSide.SELL,
        client_order_id="sold",
        intent_id=uuid4(),
        status=OrderStatus.FILLED,
        filled_quantity=entry.quantity,
    )
    await execution.store.save_order(sold)
    lagging = await execution.snapshot(opened.deployment_id or "")
    assert book_value(book, lagging, Decimal(1)) is None


class _CapturingExecution(_Execution):
    """Record the order request a desk sends."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[DiscretionaryOrderRequest] = []

    async def open_book(self, request: DiscretionaryOrderRequest) -> DeploymentSnapshot:
        self.requests.append(request)
        return await super().open_book(request)


async def test_the_reservation_check_carries_the_whole_allocation() -> None:
    """The gate is handed the allocation itself, not the requested notional before rounding."""
    store, _, desk_id = await _desk()
    execution = _CapturingExecution()
    first = await _wake(store, execution, desk_id, _open(fraction="0.1"))
    second = await _wake(store, execution, desk_id, _open(product="ETH-USD", fraction="0.1"))
    assert first.outcome is WakeOutcome.APPLIED, first.detail
    assert second.outcome is WakeOutcome.APPLIED, second.detail
    books = await store.list_books(desk_id)
    assert [request.desk_allocation_quote for request in execution.requests] == [
        book.allocated_quote for book in books
    ]
    assert [request.desk_committed_quote for request in execution.requests] == [
        Decimal(0),
        books[0].allocated_quote,
    ]
