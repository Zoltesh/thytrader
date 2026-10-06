"""Nullable venue observations survive restart without backfilling historical certainty."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os

from alembic.config import Config
from pydantic import SecretStr
import pytest

from alembic import command
from tests.execution.test_venue_observation import observation_order
from tests.persistence.test_migration_0048_strategy_root import _alembic, scratch_database
from thytrader.execution.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    IntentPurpose,
    OrderIntent,
    RuntimePhase,
)
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_execution import PostgresExecutionStore

__all__ = ["scratch_database"]
pytestmark = pytest.mark.skipif(
    os.getenv("THYTRADER_TEST_DATABASE_URL") is None,
    reason="An isolated PostgreSQL test database is required.",
)


@pytest.mark.anyio
async def test_venue_observation_restart_and_nullable_migration(
    scratch_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retain a real timestamp across local writes/restart; legacy migration adds only NULL."""
    migrated = _alembic(scratch_database, "head")
    assert migrated.returncode == 0, migrated.stderr
    observed_at = datetime.now(UTC)
    order = replace(observation_order(), venue_observed_at=observed_at)
    bot = Deployment(
        id=order.deployment_id,
        strategy_fingerprint=None,
        strategy_id=None,
        product_id=order.product_id,
        mode=DeploymentMode.LIVE,
        kind=DeploymentKind.DISCRETIONARY,
        status=DeploymentStatus.STOPPED,
        phase=RuntimePhase.FLAT,
        cash=Decimal("0"),
        created_at=observed_at,
        updated_at=observed_at,
        timeframe="1m",
    )
    engine = create_engine(SecretStr(scratch_database))
    try:
        store = PostgresExecutionStore(engine)
        await store.create_deployment(bot)
        await store.save_intent(
            OrderIntent(
                id=order.intent_id,
                deployment_id=bot.id,
                client_order_id=order.client_order_id,
                purpose=IntentPurpose.STOP,
                side=order.side,
                kind=order.kind,
                quantity=order.quantity,
                price=order.price,
                created_at=observed_at,
                candle_starts_at=observed_at,
                product_id=order.product_id,
            )
        )
        await store.save_order(order)
        await store.save_order(replace(order, updated_at=observed_at + timedelta(minutes=1)))
    finally:
        await dispose(engine)
    engine = create_engine(SecretStr(scratch_database))
    try:
        recovered = await PostgresExecutionStore(engine).get_deployment(bot.id)
        assert recovered.orders[0].venue_observed_at == observed_at
        assert recovered.orders[0].updated_at > observed_at
    finally:
        await dispose(engine)
    # Reverse only this metadata addition, then upgrade it again around an existing order.
    monkeypatch.setenv("THYTRADER_DATABASE_URL", scratch_database)
    await asyncio.to_thread(command.downgrade, Config("alembic.ini"), "0064")
    migrated = _alembic(scratch_database, "head")
    assert migrated.returncode == 0, migrated.stderr
    engine = create_engine(SecretStr(scratch_database))
    try:
        legacy = await PostgresExecutionStore(engine).get_deployment(bot.id)
        assert legacy.orders[0].id == order.id
        assert legacy.orders[0].venue_observed_at is None
        assert legacy.orders[0].quantity == order.quantity
    finally:
        await dispose(engine)
