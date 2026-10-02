"""Live PostgreSQL tests for the 0059 history-floor repair migration (ADR 0095).

Before ADR 0095 the worker recorded ``history_floor_at`` at interior no-trade gaps and from
forward walks, pinning sparse series to their newest island. 0059 clears every floor so
affected series backfill again; the worker re-proves genuine listing floors itself.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.persistence.test_migration_0048_strategy_root import _alembic, scratch_database
from tests.persistence.test_migration_0052_lookback_ceilings import _downgrade

__all__ = ["scratch_database"]

pytestmark = pytest.mark.skipif(
    os.getenv("THYTRADER_TEST_DATABASE_URL") is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL migration coverage.",
)

_NOW = datetime(2026, 10, 2, 11, 13, tzinfo=UTC)
_FLOOR = datetime(2026, 10, 2, 10, 57, tzinfo=UTC)
_STATE_ROW = (
    "INSERT INTO market_data_worker_state (provider, product_id, timeframe, status, "
    "last_attempt_at, last_success_at, requested_starts_at, requested_ends_at, "
    "covered_starts_at, covered_ends_at, expected_candle_count, received_candle_count, "
    "gap_count, missing_intervals, complete, content_fingerprint, consecutive_failures, "
    "updated_at, history_floor_at) VALUES ('coinbase', :product, '1m', 'succeeded', :now, "
    ":now, :floor, :now, :floor, :now, 16, 16, 0, 0, true, :fingerprint, 0, :now, :history)"
)


async def _seed(database_url: str) -> None:
    """Insert one floored sparse series and one series without a floor."""
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            for product, history in (("BONK-USD", _FLOOR), ("BTC-USD", None)):
                await connection.execute(
                    text(_STATE_ROW),
                    {
                        "product": product,
                        "now": _NOW,
                        "floor": _FLOOR,
                        "history": history,
                        "fingerprint": "sha256:" + "c" * 64,
                    },
                )
    finally:
        await engine.dispose()


async def _rows(database_url: str) -> list[tuple[object, ...]]:
    """Return product, floor, coverage start, and fingerprint for every seeded row."""
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT product_id, history_floor_at, covered_starts_at, content_fingerprint "
                    "FROM market_data_worker_state ORDER BY product_id"
                )
            )
            return [tuple(row) for row in result]
    finally:
        await engine.dispose()


async def _column_comment(database_url: str) -> str | None:
    """Return the ``history_floor_at`` column comment."""
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT col_description('market_data_worker_state'::regclass, attnum) "
                    "FROM pg_attribute WHERE attrelid = 'market_data_worker_state'::regclass "
                    "AND attname = 'history_floor_at'"
                )
            )
            value = result.scalar_one()
            return value if isinstance(value, str) else None
    finally:
        await engine.dispose()


def test_0059_clears_every_recorded_floor_and_keeps_coverage(scratch_database: str) -> None:
    """Floors become NULL; coverage facts stay, so prefix backfill resumes from them."""
    seeded = _alembic(scratch_database, "0058")
    assert seeded.returncode == 0, seeded.stderr
    asyncio.run(_seed(scratch_database))

    upgraded = _alembic(scratch_database, "head")
    assert upgraded.returncode == 0, upgraded.stderr

    rows = asyncio.run(_rows(scratch_database))
    assert [(row[0], row[1]) for row in rows] == [("BONK-USD", None), ("BTC-USD", None)]
    assert all(row[2] == _FLOOR for row in rows), "coverage is untouched"
    comment = asyncio.run(_column_comment(scratch_database))
    assert comment is not None
    assert "ADR 0095" in comment


def test_0059_downgrade_restores_the_comment_only(scratch_database: str) -> None:
    """Downgrade keeps cleared floors cleared and restores the old column comment."""
    upgraded = _alembic(scratch_database, "head")
    assert upgraded.returncode == 0, upgraded.stderr

    downgraded = _downgrade(scratch_database, "0058")
    assert downgraded.returncode == 0, downgraded.stderr

    comment = asyncio.run(_column_comment(scratch_database))
    assert (
        comment == "Confirmed provider hole directly before the island; prefix backfill stops here."
    )
