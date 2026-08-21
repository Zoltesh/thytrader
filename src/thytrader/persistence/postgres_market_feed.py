"""PostgreSQL repository for the latest public market ticker snapshot."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from thytrader.market_data.feed_state import (
    MarketFeedSnapshot,
    MarketFeedState,
    MarketFeedUnavailableError,
)
from thytrader.persistence.schema import market_feed_state

if TYPE_CHECKING:
    from sqlalchemy.engine import Row
    from sqlalchemy.ext.asyncio import AsyncEngine


class PostgresMarketFeedStateStore:
    """Transactional latest-state repository for the public ticker feed."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind feed-state operations to one managed async engine."""
        self._engine = engine

    async def record(self, snapshot: MarketFeedSnapshot) -> None:
        """Upsert the latest ticker lifecycle facts for one product."""
        values = {
            "product_id": snapshot.product_id,
            "state": snapshot.state.value,
            "last_message_at": snapshot.last_message_at,
            "last_ticker_at": snapshot.last_ticker_at,
            "last_price": str(snapshot.last_price) if snapshot.last_price is not None else None,
            "updated_at": snapshot.updated_at,
        }
        statement = insert(market_feed_state).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["product_id"],
            set_={
                "state": snapshot.state.value,
                "last_message_at": snapshot.last_message_at,
                "last_ticker_at": snapshot.last_ticker_at,
                "last_price": values["last_price"],
                "updated_at": snapshot.updated_at,
            },
        )
        try:
            async with self._engine.begin() as connection:
                await connection.execute(statement)
        except SQLAlchemyError as error:
            raise MarketFeedUnavailableError("Market-feed state storage is unavailable.") from error

    async def get(self, product_id: str) -> MarketFeedSnapshot | None:
        """Return the latest snapshot, or None when none has been recorded."""
        statement = select(market_feed_state).where(market_feed_state.c.product_id == product_id)
        try:
            async with self._engine.connect() as connection:
                row = (await connection.execute(statement)).one_or_none()
        except SQLAlchemyError as error:
            raise MarketFeedUnavailableError("Market-feed state storage is unavailable.") from error
        if row is None:
            return None
        return _to_snapshot(row)


def _to_snapshot(row: Row[tuple[object, ...]]) -> MarketFeedSnapshot:
    """Map one SQL row into a validated domain snapshot."""
    last_price_raw = row.last_price
    last_price: Decimal | None
    if last_price_raw is None:
        last_price = None
    else:
        try:
            last_price = Decimal(str(last_price_raw))
        except (InvalidOperation, ValueError) as error:
            raise MarketFeedUnavailableError("Stored market-feed price is invalid.") from error
    return MarketFeedSnapshot(
        product_id=str(row.product_id),
        state=MarketFeedState(str(row.state)),
        last_message_at=row.last_message_at,
        last_ticker_at=row.last_ticker_at,
        last_price=last_price,
        updated_at=row.updated_at,
    )
