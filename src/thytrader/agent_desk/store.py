"""Desk persistence protocol and an in-memory store (ADR 0147).

The process wires the PostgreSQL store; domain code only sees :class:`DeskStore`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.agent_desk.models import Desk, DeskBook, DeskWake


class DeskConflictError(RuntimeError):
    """A write lost a revision race or named a missing desk; read fresh state and retry."""


class DeskStoreUnavailableError(RuntimeError):
    """Desk storage could not be read or written; nothing is assumed."""


class DeskStore(Protocol):
    """Desks, their books and their journaled wakes."""

    async def create_desk(self, desk: Desk) -> Desk:
        """Insert a new desk."""
        ...

    async def get_desk(self, desk_id: str) -> Desk | None:
        """Return one desk or None."""
        ...

    async def list_desks(self) -> tuple[Desk, ...]:
        """Return every desk, newest first."""
        ...

    async def save_desk(
        self, desk: Desk, *, expected_revision: int, books: Sequence[DeskBook] = ()
    ) -> Desk:
        """Replace a desk when its stored revision matches; the new revision is +1.

        ``books`` are inserted or replaced in the same write, so pool cash and the books it
        funded or settled never disagree.
        """
        ...

    async def list_books(self, desk_id: str) -> tuple[DeskBook, ...]:
        """Return every book of a desk, oldest first."""
        ...

    async def add_wake(self, wake: DeskWake) -> None:
        """Journal one wake."""
        ...

    async def list_wakes(self, desk_id: str, *, limit: int) -> tuple[DeskWake, ...]:
        """Return the newest wakes of a desk, newest first."""
        ...


class InMemoryDeskStore:
    """Process-local desk store for tests and database-less runs."""

    def __init__(self) -> None:
        """Start empty."""
        self.desks: dict[str, Desk] = {}
        self.books: dict[str, DeskBook] = {}
        self.wakes: list[DeskWake] = []

    async def create_desk(self, desk: Desk) -> Desk:
        """Insert a new desk."""
        if desk.desk_id in self.desks:
            raise DeskConflictError(f"Desk {desk.desk_id} already exists.")
        self.desks[desk.desk_id] = desk
        return desk

    async def get_desk(self, desk_id: str) -> Desk | None:
        """Return one desk or None."""
        return self.desks.get(desk_id)

    async def list_desks(self) -> tuple[Desk, ...]:
        """Return every desk, newest first."""
        return tuple(sorted(self.desks.values(), key=lambda d: d.created_at, reverse=True))

    async def save_desk(
        self, desk: Desk, *, expected_revision: int, books: Sequence[DeskBook] = ()
    ) -> Desk:
        """Replace a desk and its changed books when the stored revision matches."""
        current = self.desks.get(desk.desk_id)
        if current is None or current.revision != expected_revision:
            raise DeskConflictError("The desk changed; read it again before saving.")
        saved = desk.model_copy(update={"revision": expected_revision + 1})
        self.desks[desk.desk_id] = saved
        for book in books:
            self.books[book.deployment_id] = book
        return saved

    async def list_books(self, desk_id: str) -> tuple[DeskBook, ...]:
        """Return every book of a desk, oldest first."""
        return tuple(
            sorted(
                (book for book in self.books.values() if book.desk_id == desk_id),
                key=lambda book: book.opened_at,
            )
        )

    async def add_wake(self, wake: DeskWake) -> None:
        """Journal one wake."""
        self.wakes.append(wake)

    async def list_wakes(self, desk_id: str, *, limit: int) -> tuple[DeskWake, ...]:
        """Return the newest wakes of a desk, newest first."""
        rows = [wake for wake in self.wakes if wake.desk_id == desk_id]
        return tuple(sorted(rows, key=lambda wake: wake.started_at, reverse=True)[:limit])
