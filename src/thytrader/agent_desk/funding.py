"""Funding a desk open so the pool and its books agree through any failure (ADR 0147).

1. Reserve: one write debits the pool and records a :class:`PendingOpen`.
2. Place the order (idempotent under the desk's key).
3. Link: one write clears the reservation, links the book and journals the wake.

A failed placement, or a reservation a crash left behind, is resolved by the book id the
reservation chose before placing: a book with that id (in the desk's mode, on the product) is
linked, paid for by the reservation; otherwise the reservation is refunded. The pool never
pays for a book it does not own, nor owns one unpaid.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from thytrader.agent_desk.models import (
    DeskAction,
    DeskBook,
    DeskWake,
    PendingOpen,
    WakeOutcome,
)
from thytrader.agent_desk.ports import DeskError, journal_id, save_desk
from thytrader.ids import uuid7
from thytrader.trading.models import PositionSide

if TYPE_CHECKING:
    from decimal import Decimal

    from thytrader.agent_desk.models import Desk
    from thytrader.agent_desk.ports import DeskExecution
    from thytrader.agent_desk.store import DeskStore
    from thytrader.execution.discretionary_request import DiscretionaryOrderRequest

RECOVERED_DETAIL = "An open interrupted before it was linked was found and linked on recovery."


async def open_funded(
    desk: Desk,
    *,
    request: DiscretionaryOrderRequest,
    allocation: Decimal,
    wake: DeskWake,
    now: datetime,
    store: DeskStore,
    execution: DeskExecution,
) -> DeskWake:
    """Reserve, place and link one open; return the applied wake, journaled with the link.

    The reservation names the book's id before placing, so recovery finds exactly that book.
    If placement fails after the book exists, the book is linked and its applied wake returned.
    """
    if await execution.intent_book(request.idempotency_key) is not None:
        # The answer already placed its order (a retry or replay): never fund it twice.
        message = "DESK_DUPLICATE_ANSWER: an order was already placed for this briefing."
        raise DeskError(message)
    pending = PendingOpen(
        wake_id=wake.wake_id,
        idempotency_key=request.idempotency_key,
        briefing_fingerprint=wake.briefing_fingerprint,
        deployment_id=str(uuid7(now)),
        mode=desk.mode,
        product_id=request.product_id,
        side="short" if request.side is PositionSide.SHORT else "long",
        allocated_quote=allocation,
        reserved_at=now,
        thesis=wake.thesis,
    )
    reserved = await save_desk(
        desk.model_copy(update={"cash": desk.cash - allocation, "pending_open": pending}),
        store=store,
        previous=desk,
    )
    try:
        snapshot = await execution.open_book(replace(request, book_id=UUID(pending.deployment_id)))
    except Exception as error:
        _, linked = await resolve_pending(reserved, store=store, execution=execution, wake=wake)
        if linked is None:
            raise
        return linked.model_copy(update={"detail": f"placement raised: {error}"[:500]})
    applied = wake.model_copy(
        update={"outcome": WakeOutcome.APPLIED, "deployment_id": str(snapshot.deployment.id)}
    )
    await save_desk(
        reserved.model_copy(update={"pending_open": None}),
        store=store,
        previous=reserved,
        books=(_book(reserved.desk_id, pending, str(snapshot.deployment.id)),),
        wakes=(applied,),
    )
    return applied


async def resolve_pending(
    desk: Desk,
    *,
    store: DeskStore,
    execution: DeskExecution,
    wake: DeskWake | None = None,
) -> tuple[Desk, DeskWake | None]:
    """Link the book a reservation created, or refund the reservation when there is none.

    Returns the desk and, when a book was linked, its applied wake (``wake`` when the caller
    still holds the decision, else a rebuilt ``DESK_OPEN_RECOVERED`` row unless already
    journaled).
    """
    pending = desk.pending_open
    if pending is None:
        return desk, None
    deployment_id = await execution.find_book(pending)
    if deployment_id is None:
        # Journal the refund with it, so the next wake shows the model its open never placed.
        refunded = await save_desk(
            desk.model_copy(
                update={"cash": desk.cash + pending.allocated_quote, "pending_open": None}
            ),
            store=store,
            previous=desk,
            wakes=(_refund_wake(desk, pending),),
        )
        return refunded, None
    linked = (
        wake.model_copy(update={"outcome": WakeOutcome.APPLIED, "deployment_id": deployment_id})
        if wake is not None
        else _recovered_wake(desk, pending, deployment_id)
    )
    journal = () if await _journaled(store, desk.desk_id, pending.wake_id) else (linked,)
    saved = await save_desk(
        desk.model_copy(update={"pending_open": None}),
        store=store,
        previous=desk,
        books=(_book(desk.desk_id, pending, deployment_id),),
        wakes=journal,
    )
    return saved, linked


def _book(desk_id: str, pending: PendingOpen, deployment_id: str) -> DeskBook:
    """The desk book a reservation funded."""
    return DeskBook(
        desk_id=desk_id,
        deployment_id=deployment_id,
        product_id=pending.product_id,
        side=pending.side,
        allocated_quote=pending.allocated_quote,
        opened_at=pending.reserved_at,
        wake_id=pending.wake_id,
    )


def _refund_wake(desk: Desk, pending: PendingOpen) -> DeskWake:
    """The journal row for an open that was reserved but never placed, now refunded."""
    now = datetime.now(UTC)
    return DeskWake(
        wake_id=journal_id(now),
        desk_id=desk.desk_id,
        started_at=min(pending.reserved_at, now),
        finished_at=now,
        harness="thytrader",
        briefing_fingerprint=pending.briefing_fingerprint,
        raw_response="",
        action=DeskAction.OPEN,
        outcome=WakeOutcome.REFUSED,
        outcome_code="DESK_OPEN_REFUNDED",
        detail=(
            f"An interrupted open on {pending.product_id} was never placed; its "
            f"{pending.allocated_quote} reservation was refunded."
        ),
        thesis=pending.thesis,
    )


def _recovered_wake(desk: Desk, pending: PendingOpen, deployment_id: str) -> DeskWake:
    """The journal row for an open whose own wake was lost with the interrupted link."""
    return DeskWake(
        wake_id=pending.wake_id,
        desk_id=desk.desk_id,
        started_at=pending.reserved_at,
        finished_at=pending.reserved_at,
        harness="thytrader",
        briefing_fingerprint=pending.briefing_fingerprint,
        raw_response="",
        action=DeskAction.OPEN,
        outcome=WakeOutcome.APPLIED,
        outcome_code="DESK_OPEN_RECOVERED",
        detail=RECOVERED_DETAIL,
        thesis=pending.thesis,
        deployment_id=deployment_id,
    )


async def _journaled(store: DeskStore, desk_id: str, wake_id: str) -> bool:
    """Whether the wake is already in the recent journal."""
    return any(wake.wake_id == wake_id for wake in await store.list_wakes(desk_id, limit=50))
