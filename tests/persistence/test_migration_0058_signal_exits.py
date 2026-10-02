"""Live PostgreSQL tests for the 0058 signal-exit migration (ADR 0093).

0058 adds ``execution_positions.signal_exit_bar`` (the durable "exiting on a signal"
marker) and admits the ``signal_exit`` why-trade purpose and signal kind. A downgrade
must refuse while a row depends on either.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import os
from uuid import UUID

from pydantic import SecretStr
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from tests.persistence.test_migration_0048_strategy_root import _alembic, scratch_database
from tests.persistence.test_migration_0052_lookback_ceilings import _downgrade
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_execution import PostgresExecutionStore

__all__ = ["scratch_database"]

pytestmark = pytest.mark.skipif(
    os.getenv("THYTRADER_TEST_DATABASE_URL") is None,
    reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL migration coverage.",
)

_NOW = datetime(2026, 10, 2, 12, tzinfo=UTC)
_DEPLOYMENT = UUID("01985cf0-7b60-7000-8000-00000000cc01")
_INTENT = UUID("01985cf0-7b60-7000-8000-00000000cc02")
_REASON = UUID("01985cf0-7b60-7000-8000-00000000cc03")
_PARAMETERS: dict[str, object] = {
    "now": _NOW,
    "deployment": str(_DEPLOYMENT),
    "intent": str(_INTENT),
    "reason": str(_REASON),
}
_DEPLOYMENT_ROW = (
    "INSERT INTO deployments (id, product_id, mode, status, kind, cash, phase, timeframe, "
    "created_at, updated_at) VALUES (CAST(:deployment AS uuid), 'BTC-USD', 'live', "
    "'running', 'discretionary', '1000', 'open', '1h', :now, :now)"
)
_MARKED_POSITION = (
    "INSERT INTO execution_positions (deployment_id, product_id, quantity, entry_price, "
    "stop_price, target_price, entered_bar, updated_at, signal_exit_bar) VALUES "
    "(CAST(:deployment AS uuid), 'BTC-USD', '0.01', '100', '90', NULL, :now, :now, :now)"
)
_SIGNAL_EXIT_REASON = (
    "INSERT INTO trade_reason_records (id, created_at, origin, intent_id, deployment_id, "
    "deployment_kind, mode, product_id, purpose, side, signal_kind, candle_starts_at, "
    "timeframe, risk_decision, risk_reason_code, risk_detail, policy_fingerprint, "
    "policy_source) VALUES (CAST(:reason AS uuid), :now, 'runtime', CAST(:intent AS uuid), "
    "CAST(:deployment AS uuid), 'strategy', 'live', 'BTC-USD', 'signal_exit', 'sell', "
    "'signal_exit', :now, '1h', 'allow', 'ALLOWED', 'risk-reducing exit', "
    "'sha256:" + "0" * 64 + "', 'compiled_default')"
)


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


async def _marker_round_trip(database_url: str) -> datetime | None:
    """Load the seeded book through the application store and return its marker."""
    engine = create_engine(SecretStr(database_url))
    try:
        snapshot = await PostgresExecutionStore(engine).get_deployment(_DEPLOYMENT)
        return None if snapshot.position is None else snapshot.position.signal_exit_bar
    finally:
        await dispose(engine)


def test_0058_stores_the_exit_marker_and_the_signal_exit_purpose(scratch_database: str) -> None:
    """The upgraded schema keeps the marker and admits signal_exit why-trade rows."""
    upgraded = _alembic(scratch_database, "head")
    assert upgraded.returncode == 0, upgraded.stderr
    asyncio.run(_execute(scratch_database, _DEPLOYMENT_ROW, _MARKED_POSITION, _SIGNAL_EXIT_REASON))
    assert asyncio.run(_marker_round_trip(scratch_database)) == _NOW
    nullable = asyncio.run(
        _scalar(
            scratch_database,
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'execution_positions' AND column_name = 'signal_exit_bar'",
        )
    )
    assert nullable == "YES"


def test_0058_downgrade_refuses_rows_older_code_cannot_read(scratch_database: str) -> None:
    """A marked position or a signal_exit why-trade row blocks the downgrade until removed."""
    upgraded = _alembic(scratch_database, "head")
    assert upgraded.returncode == 0, upgraded.stderr
    asyncio.run(_execute(scratch_database, _DEPLOYMENT_ROW, _MARKED_POSITION, _SIGNAL_EXIT_REASON))
    refused = _downgrade(scratch_database, "0057")
    assert refused.returncode != 0
    assert "Cannot downgrade 0058" in refused.stderr
    asyncio.run(
        _execute(
            scratch_database,
            "DELETE FROM trade_reason_records",
            "DELETE FROM execution_positions",
        )
    )
    downgraded = _downgrade(scratch_database, "0057")
    assert downgraded.returncode == 0, downgraded.stderr
    column = asyncio.run(
        _scalar(
            scratch_database,
            "SELECT COUNT(*) FROM information_schema.columns WHERE "
            "table_name = 'execution_positions' AND column_name = 'signal_exit_bar'",
        )
    )
    assert column == 0
    with pytest.raises(IntegrityError):
        asyncio.run(_execute(scratch_database, _SIGNAL_EXIT_REASON))
