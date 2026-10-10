"""Migration 0071 creates the futures tables; its downgrade refuses to drop funding history."""

from __future__ import annotations

import os

from pydantic import SecretStr
import pytest
from sqlalchemy import text

from tests.persistence.test_migration_0048_strategy_root import _alembic, scratch_database
from thytrader.persistence.database import create_engine, dispose

__all__ = ["scratch_database"]
pytestmark = pytest.mark.skipif(
    os.getenv("THYTRADER_TEST_DATABASE_URL") is None,
    reason="An isolated PostgreSQL test database is required.",
)


@pytest.mark.anyio
async def test_0071_round_trips_empty_and_refuses_to_drop_recorded_funding(
    scratch_database: str,
) -> None:
    """An empty schema downgrades cleanly; recorded funding history blocks the downgrade."""
    assert _alembic(scratch_database, "head").returncode == 0
    assert _alembic(scratch_database, "0070", operation="downgrade").returncode == 0
    assert _alembic(scratch_database, "head").returncode == 0
    engine = create_engine(SecretStr(scratch_database))
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO futures_funding_rates (product_id, funding_time, rate, "
                    "interval_seconds, first_observed_at, last_observed_at, observation_count) "
                    "VALUES ('BIP-20DEC30-CDE', '2026-10-10T00:00:00Z', '0.000009', 3600, "
                    "'2026-10-10T00:05:00Z', '2026-10-10T00:05:00Z', 1)"
                )
            )
    finally:
        await dispose(engine)
    refused = _alembic(scratch_database, "0070", operation="downgrade")
    assert refused.returncode != 0
    assert "funding history" in refused.stderr
