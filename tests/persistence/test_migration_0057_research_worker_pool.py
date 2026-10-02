"""Live PostgreSQL tests for the 0057 research worker pool migration (ADR 0092).

0057 adds lease columns (``lease_owner``, ``lease_expires_at``, ``attempts``) to
``research_jobs`` and ``portfolio_backtest_jobs``, ``research_jobs.error_code``, and the
``research_workers`` slot table. Downgrade removes them again.
"""

from __future__ import annotations

import asyncio
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

_LEASE_COLUMNS = {"lease_owner", "lease_expires_at", "attempts"}


def _columns(database_url: str, table: str) -> set[str]:
    """Return the column names of one table."""

    async def read() -> set[str]:
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                result = await connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = :table"
                    ),
                    {"table": table},
                )
                return {str(row[0]) for row in result}
        finally:
            await engine.dispose()

    return asyncio.run(read())


def test_0057_adds_and_removes_leases_error_codes_and_worker_slots(
    scratch_database: str,
) -> None:
    """Upgrade adds the worker-pool columns and table; downgrade to 0056 removes them."""
    upgraded = _alembic(scratch_database, "head")
    assert upgraded.returncode == 0, upgraded.stderr
    assert _LEASE_COLUMNS | {"error_code"} <= _columns(scratch_database, "research_jobs")
    assert _columns(scratch_database, "portfolio_backtest_jobs") >= _LEASE_COLUMNS
    assert {"slot", "pool_size", "pid", "state", "rss_bytes", "heartbeat_at"} <= _columns(
        scratch_database, "research_workers"
    )
    downgraded = _downgrade(scratch_database, "0056")
    assert downgraded.returncode == 0, downgraded.stderr
    assert not (_LEASE_COLUMNS | {"error_code"}) & _columns(scratch_database, "research_jobs")
    assert not _LEASE_COLUMNS & _columns(scratch_database, "portfolio_backtest_jobs")
    assert _columns(scratch_database, "research_workers") == set()
    again = _alembic(scratch_database, "head")
    assert again.returncode == 0, again.stderr
