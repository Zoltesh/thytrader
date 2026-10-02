"""Live PostgreSQL tests for the 0055 research-correctness migration (ADR 0090).

0055 adds ``published_backtest_results.diagnostics_json``, admits ``stop_limit`` order
intents (live stop-only protection), and lets ``execution_positions.target_price`` be
NULL (no take-profit). A downgrade must refuse while a row depends on either.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import os
from uuid import UUID

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

_NOW = datetime(2026, 10, 2, 12, tzinfo=UTC)
_DEPLOYMENT = UUID("01985cf0-7b60-7000-8000-00000000bb01")
_INTENT = UUID("01985cf0-7b60-7000-8000-00000000bb02")


async def _execute(database_url: str, *statements: str) -> None:
    """Run statements in one transaction against the scratch database."""
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            for statement in statements:
                await connection.execute(text(statement), _PARAMETERS)
    finally:
        await engine.dispose()


async def _scalar(database_url: str, statement: str) -> object:
    """Return one scalar from the scratch database."""
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return (await connection.execute(text(statement))).scalar_one()
    finally:
        await engine.dispose()


_PARAMETERS: dict[str, object] = {
    "now": _NOW,
    "deployment": str(_DEPLOYMENT),
    "intent": str(_INTENT),
}
_SEED = (
    "INSERT INTO deployments (id, product_id, mode, status, kind, cash, phase, timeframe, "
    "created_at, updated_at) VALUES (CAST(:deployment AS uuid), 'BTC-USD', 'live', "
    "'running', 'discretionary', '1000', 'open', '1h', :now, :now)",
    "INSERT INTO execution_positions (deployment_id, product_id, quantity, entry_price, "
    "stop_price, target_price, entered_bar, updated_at) VALUES (CAST(:deployment AS uuid), "
    "'BTC-USD', '0.01', '100', '90', NULL, :now, :now)",
    "INSERT INTO order_intents (id, deployment_id, client_order_id, purpose, side, kind, "
    "price, stop_trigger_price, quantity, candle_starts_at, status, product_id, created_at) "
    "VALUES (CAST(:intent AS uuid), CAST(:deployment AS uuid), 'stop-1', 'bracket', 'sell', "
    "'stop_limit', '85.5', '90', '0.01', :now, 'pending', 'BTC-USD', :now)",
)


def test_0055_admits_stop_limit_intents_untargeted_positions_and_diagnostics(
    scratch_database: str,
) -> None:
    """The upgraded schema stores every ADR 0090 shape."""
    upgraded = _alembic(scratch_database, "head")
    assert upgraded.returncode == 0, upgraded.stderr
    asyncio.run(_execute(scratch_database, *_SEED))
    nullable = asyncio.run(
        _scalar(
            scratch_database,
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'execution_positions' AND column_name = 'target_price'",
        )
    )
    assert nullable == "YES"
    diagnostics_column = asyncio.run(
        _scalar(
            scratch_database,
            "SELECT COUNT(*) FROM information_schema.columns WHERE "
            "table_name = 'published_backtest_results' AND column_name = 'diagnostics_json'",
        )
    )
    assert diagnostics_column == 1


def test_0055_downgrade_refuses_rows_older_code_cannot_read(scratch_database: str) -> None:
    """A stop_limit intent or untargeted position blocks the downgrade until removed."""
    upgraded = _alembic(scratch_database, "head")
    assert upgraded.returncode == 0, upgraded.stderr
    asyncio.run(_execute(scratch_database, *_SEED))
    refused = _downgrade(scratch_database, "0054")
    assert refused.returncode != 0
    assert "Cannot downgrade 0055" in refused.stderr
    asyncio.run(
        _execute(
            scratch_database,
            "DELETE FROM order_intents",
            "DELETE FROM execution_positions",
        )
    )
    downgraded = _downgrade(scratch_database, "0054")
    assert downgraded.returncode == 0, downgraded.stderr
    diagnostics_column = asyncio.run(
        _scalar(
            scratch_database,
            "SELECT COUNT(*) FROM information_schema.columns WHERE "
            "table_name = 'published_backtest_results' AND column_name = 'diagnostics_json'",
        )
    )
    assert diagnostics_column == 0
