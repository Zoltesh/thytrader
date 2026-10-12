"""Disposable 0076 migration roundtrip and conservative legacy duty recovery."""

from __future__ import annotations

from dataclasses import replace
import os

from pydantic import SecretStr
import pytest

from tests.persistence.test_migration_0048_strategy_root import _alembic, scratch_database
from tests.persistence.test_postgres_futures_books import _deployment
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.trading.models import DeploymentMode, LifecycleCommand

__all__ = ["scratch_database"]
pytestmark = pytest.mark.skipif(
    os.getenv("THYTRADER_TEST_DATABASE_URL") is None, reason="Disposable PostgreSQL required."
)


@pytest.mark.anyio
async def test_0076_preserves_legacy_duties_and_refuses_unsafe_downgrade(
    scratch_database: str,
) -> None:
    """Legacy diagnostic intent becomes pending, never a successful observation stamp."""
    assert _alembic(scratch_database, "head").returncode == 0
    engine = create_engine(SecretStr(scratch_database))
    try:
        store = PostgresExecutionStore(engine)
        live = replace(
            _deployment(),
            mode=DeploymentMode.LIVE,
            paper_starting_cash=None,
            paper_maker_fee_rate=None,
            paper_taker_fee_rate=None,
            lifecycle_command=LifecycleCommand.FLATTEN,
            mismatch_detail="FUTURES_DERISK_PENDING: synthetic obligation",
        )
        spot = replace(_deployment(), product_id="BTC-USD")
        await store.create_deployment(live)
        await store.create_deployment(spot)
        assert _alembic(scratch_database, "0075", operation="downgrade").returncode == 0
        assert _alembic(scratch_database, "head").returncode == 0
        recovered = await PostgresExecutionStore(engine).get_deployment(live.id)
        assert recovered.deployment.futures_derisk_pending
        assert recovered.deployment.futures_flat_confirmation_after == live.updated_at
        untouched = await store.get_deployment(spot.id)
        assert not untouched.deployment.futures_derisk_pending
        assert untouched.deployment.futures_flat_confirmation_after is None
        assert _alembic(scratch_database, "0075", operation="downgrade").returncode != 0
        still_pending = await store.get_deployment(live.id)
        assert still_pending.deployment.futures_derisk_pending
        assert still_pending.deployment.futures_flat_confirmation_after is not None
    finally:
        await dispose(engine)
