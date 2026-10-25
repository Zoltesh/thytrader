"""What desk services share: the refusal type, the execution port and the revisioned save."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from thytrader.agent_desk.store import DeskConflictError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from decimal import Decimal

    from thytrader.agent_desk.models import Desk, DeskBook, DeskWake, PendingOpen
    from thytrader.agent_desk.store import DeskStore
    from thytrader.execution.discretionary_request import DiscretionaryOrderRequest
    from thytrader.trading.models import DeploymentSnapshot


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
    """Save under the previous revision (with books and wakes); a lost race is a conflict."""
    stamped = updated.model_copy(update={"updated_at": datetime.now(UTC)})
    try:
        return await store.save_desk(
            stamped, expected_revision=previous.revision, books=books, wakes=wakes
        )
    except DeskConflictError as error:
        raise DeskError(f"DESK_CONFLICT: {error}") from error
