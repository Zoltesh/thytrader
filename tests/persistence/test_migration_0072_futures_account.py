"""Migration 0072 creates the CFM mirror tables and downgrades cleanly (ADR 0127)."""

from __future__ import annotations

import os

import pytest

from tests.persistence.test_migration_0048_strategy_root import _alembic, scratch_database

__all__ = ["scratch_database"]
pytestmark = pytest.mark.skipif(
    os.getenv("THYTRADER_TEST_DATABASE_URL") is None,
    reason="An isolated PostgreSQL test database is required.",
)


@pytest.mark.anyio
async def test_0072_round_trips(scratch_database: str) -> None:
    """Upgrade, downgrade to 0071 and upgrade again on a scratch database."""
    assert _alembic(scratch_database, "head").returncode == 0
    assert _alembic(scratch_database, "0071", operation="downgrade").returncode == 0
    assert _alembic(scratch_database, "head").returncode == 0
