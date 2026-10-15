"""Live PostgreSQL test for the 0078 cancel time and settlement columns (ADR 0141).

Existing canceled orders, and those a failed read left UNKNOWN after the venue reported them
canceled, get their last save time as the cancel time: never earlier than the real cancel, so
settlement waits the full delay after it. Other orders get none, and no order starts settled.
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

__all__ = ["scratch_database"]

pytestmark = pytest.mark.skipif(
    os.getenv("THYTRADER_TEST_DATABASE_URL") is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL migration coverage.",
)

_CREATED = datetime(2026, 10, 1, 12, tzinfo=UTC)
_UPDATED = datetime(2026, 10, 15, 9, tzinfo=UTC)
_UNCONFIRMED = datetime(2026, 10, 15, 9, 5, tzinfo=UTC)
_DEPLOYMENT = UUID("01985cf0-7b60-7000-8000-00000000dd01")
_PARAMETERS: dict[str, object] = {
    "created": _CREATED,
    "updated": _UPDATED,
    "deployment": str(_DEPLOYMENT),
}
_DEPLOYMENT_ROW = (
    "INSERT INTO deployments (id, product_id, mode, status, kind, cash, phase, timeframe, "
    "created_at, updated_at) VALUES (CAST(:deployment AS uuid), 'BTC-USD', 'live', "
    "'paused', 'discretionary', '1000', 'flat', '1h', :created, :created)"
)


_INTENT_ROW = (
    "INSERT INTO order_intents (id, deployment_id, client_order_id, purpose, side, kind, "
    "quantity, candle_starts_at, status, product_id, created_at) VALUES "
    "(CAST(:intent AS uuid), CAST(:deployment AS uuid), :client, 'stop', 'sell', 'stop_limit', "
    "'1', :created, 'submitted', 'BTC-USD', :created)"
)
_ORDER_ROW = (
    "INSERT INTO execution_orders (id, deployment_id, intent_id, client_order_id, side, kind, "
    "quantity, filled_quantity, status, reject_reason, product_id, created_at, updated_at) "
    "VALUES (CAST(:order AS uuid), CAST(:deployment AS uuid), CAST(:intent AS uuid), :client, "
    "'sell', 'stop_limit', '1', '0', :status, :reason, 'BTC-USD', :created, :updated)"
)


async def _seed(database_url: str) -> None:
    """Insert one canceled and one open order on one book."""
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text(_DEPLOYMENT_ROW), _PARAMETERS)
            for suffix, status, reason, updated in (
                ("0c1", "canceled", None, _UPDATED),
                ("0c2", "open", None, _UPDATED),
                ("0c3", "unknown", "terminal_read_unconfirmed", _UNCONFIRMED),
            ):
                parameters = {
                    **_PARAMETERS,
                    "intent": f"01985cf0-7b60-7000-8000-00000000{suffix}a",
                    "order": f"01985cf0-7b60-7000-8000-00000000{suffix}b",
                    "client": f"client-{suffix}",
                    "status": status,
                    "reason": reason,
                    "updated": updated,
                }
                await connection.execute(text(_INTENT_ROW), parameters)
                await connection.execute(text(_ORDER_ROW), parameters)
    finally:
        await engine.dispose()


async def _rows(database_url: str) -> dict[str, tuple[object, object]]:
    """Each order's status mapped to its cancel time and settlement."""
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT status, canceled_at, settled_at FROM execution_orders")
            )
            return {str(row[0]): (row[1], row[2]) for row in result}
    finally:
        await engine.dispose()


def test_0078_backfills_canceled_orders_with_their_last_save(scratch_database: str) -> None:
    """Canceled orders get their last save time as the cancel time; others none."""
    assert _alembic(scratch_database, "0077").returncode == 0
    asyncio.run(_seed(scratch_database))
    upgraded = _alembic(scratch_database, "0078")
    assert upgraded.returncode == 0, upgraded.stderr
    rows = asyncio.run(_rows(scratch_database))
    assert rows["open"] == (None, None)
    assert rows["canceled"] == (_UPDATED, None)
    assert rows["unknown"] == (_UNCONFIRMED, None)
    downgraded = _alembic(scratch_database, "0077", operation="downgrade")
    assert downgraded.returncode == 0, downgraded.stderr
