"""What desk services share: the refusal type, the execution port and the revisioned save."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from pydantic import ValidationError

from thytrader.agent_desk.models import WakeOutcome
from thytrader.agent_desk.store import DeskConflictError
from thytrader.ids import uuid7

if TYPE_CHECKING:
    from collections.abc import Sequence
    from decimal import Decimal

    from thytrader.agent_desk.models import Desk, DeskBook, DeskWake, PendingOpen
    from thytrader.agent_desk.store import DeskStore
    from thytrader.execution.discretionary_request import DiscretionaryOrderRequest
    from thytrader.trading.models import DeploymentSnapshot, ExecutionConflictError


class DeskError(RuntimeError):
    """A desk request was refused; the message leads with a code."""


class DeskExecution(Protocol):
    """The discretionary paper book operations a desk needs (wired by the API process)."""

    async def snapshot(self, deployment_id: str) -> DeploymentSnapshot:
        """Return one book."""
        ...

    async def open_book(self, request: DiscretionaryOrderRequest) -> DeploymentSnapshot:
        """Place one discretionary order on a fresh paper book."""
        ...

    async def find_book(self, pending: PendingOpen) -> str | None:
        """The reservation's own book (its pre-chosen id, in its mode, on its product), if any.

        A store outage must raise, never read as "no book": a refund then would fund twice.
        """
        ...

    async def intent_book(self, idempotency_key: str) -> str | None:
        """The book an order placed under ``idempotency_key`` belongs to, if one was placed."""
        ...

    async def stop_book(
        self, deployment_id: str, *, flatten: bool, expected_revision: int | None = None
    ) -> DeploymentSnapshot:
        """Stop one book, flattening it when asked; refuse if it changed since that revision."""
        ...

    async def fee_rates(self) -> tuple[Decimal, Decimal] | None:
        """The account's paper maker and taker rates, or None when unreadable."""
        ...


async def save_desk(
    updated: Desk,
    *,
    store: DeskStore,
    previous: Desk,
    books: Sequence[DeskBook] = (),
    wakes: Sequence[DeskWake] = (),
) -> Desk:
    """Save under the previous revision (with books and wakes); a lost race is a conflict.

    Updates are model copies, which skip validation, so every row is validated here: a row
    that would not read back is refused ``DESK_INVALID`` and never stored.
    """
    stamped = updated.model_copy(update={"updated_at": datetime.now(UTC)})
    for row in (stamped, *books, *wakes):
        try:
            type(row).model_validate_json(row.model_dump_json())
        except ValidationError as error:
            name = type(row).__name__
            raise DeskError(f"DESK_INVALID: the {name} write fails validation.") from error
    try:
        return await store.save_desk(
            stamped, expected_revision=previous.revision, books=books, wakes=wakes
        )
    except DeskConflictError as error:
        raise DeskError(f"DESK_CONFLICT: {error}") from error


def refusal_outcome(error: DeskError | ExecutionConflictError) -> dict[str, object]:
    """The journal fields of an open refused by the desk or by the execution path."""
    if isinstance(error, DeskError):
        code, _, detail = str(error).partition(": ")
        return {"outcome": WakeOutcome.REFUSED, "outcome_code": code, "detail": detail}
    return {
        "outcome": WakeOutcome.REFUSED,
        "outcome_code": conflict_code(str(error)),
        "detail": str(error)[:500],
    }


def conflict_code(message: str) -> str:
    """A leading CODE: of an execution refusal, else a generic desk code."""
    head = message.split(":", 1)[0].strip()
    if head.isupper() and "_" in head and " " not in head:
        return head
    if "occupied discretionary book" in message:
        return "DESK_PRODUCT_OCCUPIED"
    if "risk-policy allocations are in force" in message:
        return "DISCRETIONARY_NOT_ALLOCATED"
    if message.startswith("Desk allocation") or "allocation exceeded" in message.lower():
        return "ALLOCATION_EXCEEDED"
    return "DESK_ORDER_REFUSED"


_LAST_WAKE_ID: list[int] = [0]


def journal_id(moment: datetime) -> str:
    """A wake id that sorts after every wake id this process minted before (journal order).

    UUIDv7 carries only milliseconds and random low bits, so two wakes journaled in the same
    millisecond (or across a clock step back) could sort backwards. The desk API is one process
    and journals wakes under the desk's lock, so bumping past the last id keeps journal order.
    """
    candidate = uuid7(moment).int
    if candidate <= _LAST_WAKE_ID[0]:
        candidate = _LAST_WAKE_ID[0] + 1
    _LAST_WAKE_ID[0] = candidate
    return str(UUID(int=candidate))
