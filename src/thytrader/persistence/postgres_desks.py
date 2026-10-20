"""PostgreSQL repository for agent desks, their books and wakes (ADR 0147)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from thytrader.agent_desk.models import Desk, DeskBook, DeskWake
from thytrader.agent_desk.store import DeskConflictError, DeskStoreUnavailableError
from thytrader.persistence.schema import desk_books, desk_wakes, desks

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine
    from sqlalchemy.sql import Executable

_UNAVAILABLE = "Agent desk storage is unavailable."


class PostgresDeskStore:
    """Desks with optimistic revisions; books and wakes as canonical JSON rows."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind to one managed async engine."""
        self._engine = engine

    async def create_desk(self, desk: Desk) -> Desk:
        """Insert a new desk."""
        statement = insert(desks).values(
            desk_id=desk.desk_id,
            revision=desk.revision,
            created_at=desk.created_at,
            updated_at=desk.updated_at,
            desk_json=desk.model_dump_json(),
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except IntegrityError as error:
            raise DeskConflictError(f"Desk {desk.desk_id} already exists.") from error
        except (SQLAlchemyError, OSError) as error:
            raise DeskStoreUnavailableError(_UNAVAILABLE) from error
        return desk

    async def get_desk(self, desk_id: str) -> Desk | None:
        """Return one desk or None."""
        rows = await self._rows(select(desks.c.desk_json).where(desks.c.desk_id == desk_id))
        return _parse(Desk, rows[0]) if rows else None

    async def list_desks(self) -> tuple[Desk, ...]:
        """Return every desk, newest first."""
        rows = await self._rows(select(desks.c.desk_json).order_by(desks.c.created_at.desc()))
        return tuple(_parse(Desk, row) for row in rows)

    async def save_desk(self, desk: Desk, *, expected_revision: int) -> Desk:
        """Replace a desk when its stored revision matches; the saved revision is +1."""
        saved = desk.model_copy(update={"revision": expected_revision + 1})
        statement = (
            update(desks)
            .where(desks.c.desk_id == desk.desk_id, desks.c.revision == expected_revision)
            .values(
                revision=saved.revision,
                updated_at=saved.updated_at,
                desk_json=saved.model_dump_json(),
            )
        )
        try:
            async with self._engine.begin() as connection:
                result = await connection.execute(statement)
        except (SQLAlchemyError, OSError) as error:
            raise DeskStoreUnavailableError(_UNAVAILABLE) from error
        if result.rowcount != 1:
            raise DeskConflictError("The desk changed; read it again before saving.")
        return saved

    async def add_book(self, book: DeskBook) -> None:
        """Record a book the desk funded."""
        await self._write(
            insert(desk_books).values(
                deployment_id=UUID(book.deployment_id),
                desk_id=book.desk_id,
                opened_at=book.opened_at,
                settled_at=book.settled_at,
                book_json=book.model_dump_json(),
            )
        )

    async def save_book(self, book: DeskBook) -> None:
        """Replace one book row (settlement)."""
        await self._write(
            update(desk_books)
            .where(desk_books.c.deployment_id == UUID(book.deployment_id))
            .values(settled_at=book.settled_at, book_json=book.model_dump_json())
        )

    async def list_books(self, desk_id: str) -> tuple[DeskBook, ...]:
        """Return every book of a desk, oldest first."""
        rows = await self._rows(
            select(desk_books.c.book_json)
            .where(desk_books.c.desk_id == desk_id)
            .order_by(desk_books.c.opened_at)
        )
        return tuple(_parse(DeskBook, row) for row in rows)

    async def add_wake(self, wake: DeskWake) -> None:
        """Journal one wake."""
        await self._write(
            insert(desk_wakes).values(
                wake_id=wake.wake_id,
                desk_id=wake.desk_id,
                started_at=wake.started_at,
                outcome=wake.outcome.value,
                wake_json=wake.model_dump_json(),
            )
        )

    async def list_wakes(self, desk_id: str, *, limit: int) -> tuple[DeskWake, ...]:
        """Return the newest wakes of a desk, newest first."""
        rows = await self._rows(
            select(desk_wakes.c.wake_json)
            .where(desk_wakes.c.desk_id == desk_id)
            .order_by(desk_wakes.c.started_at.desc())
            .limit(limit)
        )
        return tuple(_parse(DeskWake, row) for row in rows)

    async def _write(self, statement: Executable) -> None:
        """Execute one write in its own transaction."""
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except (SQLAlchemyError, OSError) as error:
            raise DeskStoreUnavailableError(_UNAVAILABLE) from error

    async def _rows(self, statement: Executable) -> list[str]:
        """Return the first column of every row."""
        try:
            async with self._engine.connect() as connection:
                return list((await connection.execute(statement)).scalars())
        except (SQLAlchemyError, OSError) as error:
            raise DeskStoreUnavailableError(_UNAVAILABLE) from error


def _parse[T: (Desk, DeskBook, DeskWake)](model: type[T], stored: str) -> T:
    """Validate one stored JSON document; malformed rows fail closed."""
    try:
        return model.model_validate_json(stored)
    except ValidationError as error:
        raise DeskStoreUnavailableError(f"A stored {model.__name__} row is malformed.") from error
