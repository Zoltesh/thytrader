"""Funding a desk open so the pool and its books agree through any failure (ADR 0147).

1. Reserve: one write debits the pool and records a :class:`PendingOpen`.
2. Place the order (idempotent under the desk's key).
3. Link: one write clears the reservation, links the book and journals the wake.

A failed placement, or a reservation a crash left behind, is resolved by looking up the key:
a book that exists is linked (it is the desk's, paid for by the reservation); otherwise the
reservation is refunded. The pool never pays for a book it does not own, nor owns one unpaid.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.agent_desk.models import (
    DeskAction,
    DeskBook,
    DeskWake,
    PendingOpen,
    WakeOutcome,
)
from thytrader.agent_desk.ports import save_desk
from thytrader.trading.models import PositionSide

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
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
    """Reserve, place and link one open; return the applied wake, journaled with the link."""
    pending = PendingOpen(
        wake_id=wake.wake_id,
        idempotency_key=request.idempotency_key,
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
        snapshot = await execution.open_book(request)
    except Exception:
        await resolve_pending(reserved, store=store, execution=execution)
        raise
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


async def resolve_pending(desk: Desk, *, store: DeskStore, execution: DeskExecution) -> Desk:
    """Link the book a reservation paid for, or refund the reservation when there is none."""
    pending = desk.pending_open
    if pending is None:
        return desk
    deployment_id = await execution.find_book(pending.idempotency_key)
    if deployment_id is None:
        return await save_desk(
            desk.model_copy(
                update={"cash": desk.cash + pending.allocated_quote, "pending_open": None}
            ),
            store=store,
            previous=desk,
        )
    linked: Sequence[DeskWake] = ()
    if not await _journaled(store, desk.desk_id, pending.wake_id):
        linked = (_recovered_wake(desk, pending, deployment_id),)
    return await save_desk(
        desk.model_copy(update={"pending_open": None}),
        store=store,
        previous=desk,
        books=(_book(desk.desk_id, pending, deployment_id),),
        wakes=linked,
    )


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


def _recovered_wake(desk: Desk, pending: PendingOpen, deployment_id: str) -> DeskWake:
    """The journal row for an open whose own wake was lost with the interrupted link."""
    return DeskWake(
        wake_id=pending.wake_id,
        desk_id=desk.desk_id,
        started_at=pending.reserved_at,
        finished_at=pending.reserved_at,
        harness="thytrader",
        briefing_fingerprint="",
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
