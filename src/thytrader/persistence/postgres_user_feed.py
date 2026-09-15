"""PostgreSQL repository for the singleton authenticated user-order feed."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.execution.user_feed_state import (
    UserOrderFeedSnapshot,
    UserOrderFeedState,
    UserOrderFeedUnavailableError,
)
from thytrader.persistence.schema import user_order_feed_state

if TYPE_CHECKING:
    from sqlalchemy.engine import Row
    from sqlalchemy.ext.asyncio import AsyncEngine

_SINGLETON_ID = 1


class PostgresUserOrderFeedStateStore:
    """Transactional latest-state repository for the user-order WebSocket."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind feed-state operations to one managed async engine."""
        self._engine = engine

    async def record(self, snapshot: UserOrderFeedSnapshot) -> None:
        """Upsert the singleton user-order feed lifecycle row."""
        values = {
            "id": _SINGLETON_ID,
            "state": snapshot.state.value,
            "last_message_at": snapshot.last_message_at,
            "last_heartbeat_at": snapshot.last_heartbeat_at,
            "updated_at": snapshot.updated_at,
        }
        statement = insert(user_order_feed_state).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["id"],
            set_={
                "state": snapshot.state.value,
                "last_message_at": snapshot.last_message_at,
                "last_heartbeat_at": snapshot.last_heartbeat_at,
                "updated_at": snapshot.updated_at,
            },
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise UserOrderFeedUnavailableError(
                "User-order feed state storage is unavailable."
            ) from error

    async def get(self) -> UserOrderFeedSnapshot | None:
        """Return the singleton snapshot, or None when none has been recorded."""
        statement = select(user_order_feed_state).where(user_order_feed_state.c.id == _SINGLETON_ID)
        try:
            async with self._engine.connect() as connection:
                row = (await connection.execute(statement)).one_or_none()
        except SQLAlchemyError as error:
            raise UserOrderFeedUnavailableError(
                "User-order feed state storage is unavailable."
            ) from error
        if row is None:
            return None
        return _to_snapshot(row)


def _to_snapshot(row: Row[tuple[object, ...]]) -> UserOrderFeedSnapshot:
    """Map one SQL row into a validated domain snapshot."""
    return UserOrderFeedSnapshot(
        state=UserOrderFeedState(str(row.state)),
        last_message_at=row.last_message_at,
        last_heartbeat_at=row.last_heartbeat_at,
        updated_at=row.updated_at,
    )
