"""Live PostgreSQL tests for the 0052 research lookback ceiling migration (ADR 0085).

Each test creates a throwaway database. The watchlist CHECK must admit the widest
per-timeframe ceiling (87600 hours) and nothing above it, the PostgreSQL store must
round-trip a ten-year watch, and a downgrade must clamp rows back to the ADR 0068
ceilings so older code never reads a row it would reject.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.persistence.test_migration_0048_strategy_root import _ROOT, _alembic, scratch_database
from thytrader.market_data.models import CandleInterval
from thytrader.market_data.watchlist import MarketDataWatchTarget
from thytrader.persistence.postgres_market_data_watchlist import PostgresMarketDataWatchlistStore

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

__all__ = ["scratch_database"]

pytestmark = pytest.mark.skipif(
    os.getenv("THYTRADER_TEST_DATABASE_URL") is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL migration coverage.",
)

_NOW = datetime(2026, 10, 2, 12, tzinfo=UTC)
_INSERT = text(
    "INSERT INTO market_data_watchlist "
    "(provider, product_id, timeframe, lookback_hours, enabled, created_at, updated_at) "
    "VALUES ('coinbase', :product, :timeframe, :hours, true, :at, :at)"
)


def _downgrade(database_url: str, target: str) -> subprocess.CompletedProcess[str]:
    """Run one Alembic downgrade against an explicit database in a subprocess."""
    environment = {**os.environ, "THYTRADER_DATABASE_URL": database_url}
    return subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "downgrade", target],
        cwd=_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


async def _insert(connection: AsyncConnection, product: str, timeframe: str, hours: int) -> None:
    """Insert one raw watchlist row, bypassing domain validation, to exercise the CHECK."""
    await connection.execute(
        _INSERT, {"product": product, "timeframe": timeframe, "hours": hours, "at": _NOW}
    )


def test_0052_admits_ten_year_watches_and_rejects_longer(scratch_database: str) -> None:
    """The widened CHECK accepts 87600 hours, refuses 87601, and the store round-trips it."""
    upgraded = _alembic(scratch_database, "head")
    assert upgraded.returncode == 0, upgraded.stderr

    async def exercise() -> None:
        engine = create_async_engine(scratch_database)
        try:
            async with engine.begin() as connection:
                await _insert(connection, "ETH-USDC", "1d", 87_600)
            with pytest.raises(IntegrityError):
                async with engine.begin() as connection:
                    await _insert(connection, "SOL-USDC", "1d", 87_601)
            store = PostgresMarketDataWatchlistStore(engine)
            stored = await store.upsert(
                MarketDataWatchTarget(
                    provider="coinbase",
                    product_id="BTC-USDC",
                    timeframe=CandleInterval.ONE_HOUR,
                    lookback_hours=43_800,
                    enabled=True,
                    updated_at=_NOW,
                )
            )
            assert stored.lookback_hours == 43_800
            listed = {target.product_id: target.lookback_hours for target in await store.list_all()}
            assert listed == {"BTC-USDC": 43_800, "ETH-USDC": 87_600}
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def test_0052_downgrade_clamps_rows_to_the_adr_0068_ceilings(scratch_database: str) -> None:
    """Downgrading never leaves a row older code would reject."""
    upgraded = _alembic(scratch_database, "head")
    assert upgraded.returncode == 0, upgraded.stderr

    async def seed() -> None:
        engine = create_async_engine(scratch_database)
        try:
            async with engine.begin() as connection:
                await _insert(connection, "BTC-USDC", "1h", 43_800)
                await _insert(connection, "ETH-USDC", "1d", 87_600)
                await _insert(connection, "SOL-USDC", "5m", 168)
        finally:
            await engine.dispose()

    async def read() -> dict[str, int]:
        engine = create_async_engine(scratch_database)
        try:
            async with engine.connect() as connection:
                rows = await connection.execute(
                    text("SELECT product_id, lookback_hours FROM market_data_watchlist")
                )
                return {str(row[0]): int(row[1]) for row in rows}
        finally:
            await engine.dispose()

    asyncio.run(seed())
    downgraded = _downgrade(scratch_database, "0051")
    assert downgraded.returncode == 0, downgraded.stderr
    assert asyncio.run(read()) == {"BTC-USDC": 2_160, "ETH-USDC": 8_760, "SOL-USDC": 168}
