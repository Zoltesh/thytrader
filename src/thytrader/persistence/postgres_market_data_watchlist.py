"""PostgreSQL repository for the market-data ingestion watchlist."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from thytrader.market_data.models import CandleInterval
from thytrader.market_data.watchlist import (
    MarketDataWatchlistError,
    MarketDataWatchTarget,
)
from thytrader.persistence.schema import market_data_watchlist

if TYPE_CHECKING:
    from sqlalchemy.engine import Row
    from sqlalchemy.ext.asyncio import AsyncEngine


class PostgresMarketDataWatchlistStore:
    """Transactional watchlist shared by the API, worker, and operator catalog."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind watchlist operations to one managed async engine."""
        self._engine = engine

    async def list_all(self) -> tuple[MarketDataWatchTarget, ...]:
        """Return every watch target in stable identity order."""
        statement = select(market_data_watchlist).order_by(
            market_data_watchlist.c.provider,
            market_data_watchlist.c.product_id,
            market_data_watchlist.c.timeframe,
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).all()
        return tuple(_to_target(row) for row in rows)

    async def list_enabled(self) -> tuple[MarketDataWatchTarget, ...]:
        """Return enabled ingestion targets in stable identity order."""
        statement = (
            select(market_data_watchlist)
            .where(market_data_watchlist.c.enabled.is_(True))
            .order_by(
                market_data_watchlist.c.provider,
                market_data_watchlist.c.product_id,
                market_data_watchlist.c.timeframe,
            )
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).all()
        return tuple(_to_target(row) for row in rows)

    async def get(
        self,
        provider: str,
        product_id: str,
        timeframe: CandleInterval,
    ) -> MarketDataWatchTarget | None:
        """Load one exact watch target."""
        statement = select(market_data_watchlist).where(
            market_data_watchlist.c.provider == provider,
            market_data_watchlist.c.product_id == product_id,
            market_data_watchlist.c.timeframe == timeframe.value,
        )
        async with self._engine.connect() as connection:
            row = (await connection.execute(statement)).first()
        return _to_target(row) if row is not None else None

    async def upsert(self, target: MarketDataWatchTarget) -> MarketDataWatchTarget:
        """Insert or replace one watch target without rewriting created_at."""
        values = {
            "provider": target.provider,
            "product_id": target.product_id,
            "timeframe": target.timeframe.value,
            "lookback_hours": target.lookback_hours,
            "enabled": target.enabled,
            "created_at": target.updated_at,
            "updated_at": target.updated_at,
            "ingest_requested_at": target.ingest_requested_at,
        }
        statement = insert(market_data_watchlist).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=["provider", "product_id", "timeframe"],
            set_={
                "lookback_hours": target.lookback_hours,
                "enabled": target.enabled,
                "updated_at": target.updated_at,
            },
        )
        async with self._engine.begin() as connection:
            await connection.execute(statement)
        stored = await self.get(target.provider, target.product_id, target.timeframe)
        if stored is None:
            raise MarketDataWatchlistError("Watch target could not be persisted.")
        return stored

    async def request_ingest(
        self,
        *,
        provider: str,
        product_id: str,
        timeframe: CandleInterval,
        lookback_hours: int,
        now: datetime,
    ) -> MarketDataWatchTarget:
        """Create or update one target and set ingest_requested_at."""
        requested_at = now.astimezone(UTC)
        existing = await self.get(provider, product_id, timeframe)
        if existing is None:
            return await self.upsert(
                MarketDataWatchTarget(
                    provider=provider,
                    product_id=product_id,
                    timeframe=timeframe,
                    lookback_hours=lookback_hours,
                    enabled=True,
                    updated_at=requested_at,
                    ingest_requested_at=requested_at,
                )
            )
        statement = (
            market_data_watchlist.update()
            .where(
                market_data_watchlist.c.provider == provider,
                market_data_watchlist.c.product_id == product_id,
                market_data_watchlist.c.timeframe == timeframe.value,
            )
            .values(ingest_requested_at=requested_at, updated_at=requested_at)
        )
        async with self._engine.begin() as connection:
            await connection.execute(statement)
        stored = await self.get(provider, product_id, timeframe)
        if stored is None:
            raise MarketDataWatchlistError("Watch target could not be persisted.")
        return stored

    async def clear_ingest_request(
        self,
        provider: str,
        product_id: str,
        timeframe: CandleInterval,
    ) -> None:
        """Clear a pending ingest request when the row exists."""
        statement = (
            market_data_watchlist.update()
            .where(
                market_data_watchlist.c.provider == provider,
                market_data_watchlist.c.product_id == product_id,
                market_data_watchlist.c.timeframe == timeframe.value,
            )
            .values(ingest_requested_at=None)
        )
        async with self._engine.begin() as connection:
            await connection.execute(statement)


def _to_target(row: Row[tuple[object, ...]]) -> MarketDataWatchTarget:
    """Reconstruct one typed watch target from a SQLAlchemy row."""
    values = row._mapping
    try:
        return MarketDataWatchTarget(
            provider=cast("str", values["provider"]),
            product_id=cast("str", values["product_id"]),
            timeframe=CandleInterval(cast("str", values["timeframe"])),
            lookback_hours=cast("int", values["lookback_hours"]),
            enabled=cast("bool", values["enabled"]),
            updated_at=cast("datetime", values["updated_at"]),
            created_at=cast("datetime | None", values.get("created_at")),
            ingest_requested_at=cast("datetime | None", values.get("ingest_requested_at")),
        )
    except (KeyError, TypeError, ValueError) as error:
        message = "Market-data watchlist has malformed persisted state."
        raise MarketDataWatchlistError(message) from error
