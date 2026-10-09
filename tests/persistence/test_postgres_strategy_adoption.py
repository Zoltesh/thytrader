"""PostgreSQL end to end: a live strategy starts holding adopted coins (ADR 0124)."""

from __future__ import annotations

from decimal import Decimal
import os
from typing import TYPE_CHECKING

from pydantic import SecretStr
import pytest
from sqlalchemy import func, select

from tests.adoption_support import balance, balance_reader
from tests.execution.decision_support import strategy
from tests.execution.test_inventory_adoption_service import _Venue
from tests.execution.test_inventory_adoption_strategy import _WIDE, _Account
from tests.persistence.test_migration_0048_strategy_root import _alembic, scratch_database
from thytrader.execution.adoption_strategy import StrategyAdoptionVenue, start_with_adoption
from thytrader.execution.paper import PaperBroker
from thytrader.execution_worker.service import _process_one
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.persistence import postgres_adoption
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_adoption import PostgresInventoryAdoptionStore
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.postgres_strategies import PostgresStrategyStore
from thytrader.persistence.schema import deployments, order_intents
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.strategies.library import create_strategy_from_definition
from thytrader.trading.models import (
    DeploymentStatus,
    ExecutionStoreError,
    OrderKind,
    OrderSide,
    RuntimePhase,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy import Table
    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.strategies.snapshots import StrategySnapshot
    from thytrader.trading.models import DeploymentSnapshot

__all__ = ["scratch_database"]
pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        os.getenv("THYTRADER_TEST_DATABASE_URL") is None,
        reason="An isolated PostgreSQL test database is required.",
    ),
]


@pytest.fixture
async def engine(scratch_database: str) -> AsyncIterator[AsyncEngine]:
    """A migrated scratch database."""
    migrated = _alembic(scratch_database, "head")
    assert migrated.returncode == 0, migrated.stderr
    created = create_engine(SecretStr(scratch_database))
    try:
        yield created
    finally:
        await dispose(created)


async def _snapshot(engine: AsyncEngine) -> StrategySnapshot:
    """The template strategy, stored and snapshotted in PostgreSQL."""
    strategies = PostgresStrategyStore(engine)
    record = await create_strategy_from_definition(strategies, strategy())
    return await strategies.snapshot(record.strategy_id)


async def _start(engine: AsyncEngine, snapshot: StrategySnapshot) -> DeploymentSnapshot:
    """Start the strategy live adopting 0.04 of 0.05 held BTC."""
    risk = InMemoryRiskPolicyStore()
    await risk.publish(compiled_default_risk_policy().model_copy(update=_WIDE))
    return await start_with_adoption(
        store=PostgresExecutionStore(engine),
        publication_store=PostgresStrategyStore(engine),
        strategy_fingerprint=snapshot.strategy_fingerprint,
        quantity=Decimal("0.04"),
        live_allowed=True,
        risk_store=risk,
        venue=StrategyAdoptionVenue(
            adoption_store=PostgresInventoryAdoptionStore(engine),
            market_data=MarketDataService(DemoMarketData()),
            read_balances=balance_reader(balance("BTC", "0.05"), balance("USD", "100000")),
            live_quote_cash=Decimal(100000),
        ),
    )


async def _count(engine: AsyncEngine, table: Table) -> int:
    """Count the rows of one table."""
    async with engine.connect() as connection:
        counted = await connection.scalar(select(func.count()).select_from(table))
    return int(counted or 0)


async def test_the_bot_and_its_adoption_persist_and_the_worker_protects_it(
    engine: AsyncEngine,
) -> None:
    """One transaction writes the bot and its OPEN position; the next cycle rests the OCO."""
    snapshot = await _snapshot(engine)
    started = await _start(engine, snapshot)
    execution = PostgresExecutionStore(engine)
    reloaded = await execution.get_deployment(started.deployment.id)
    assert reloaded.deployment.status is DeploymentStatus.RUNNING
    assert reloaded.deployment.phase is RuntimePhase.OPEN
    assert reloaded.position is not None and reloaded.position.quantity == Decimal("0.04")
    venue = _Venue()
    await _process_one(
        deployment_id=started.deployment.id,
        store=execution,
        publication_store=PostgresStrategyStore(engine),
        market_data=MarketDataService(DemoMarketData()),
        paper_broker=PaperBroker(),
        live_broker=venue,
        quote_reader=_Account(),
        risk_policy=compiled_default_risk_policy().model_copy(update=_WIDE),
        portfolio=(),
        user_feed_store=None,
        memory_store=None,
    )
    assert venue.placed == [(OrderKind.TRIGGER_BRACKET, OrderSide.SELL, Decimal("0.04"))]


async def test_a_failed_adoption_rolls_back_the_new_bot(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure after the bot row was inserted leaves neither bot nor adoption."""
    snapshot = await _snapshot(engine)

    def broken_position(*args: object, **kwargs: object) -> object:
        """Fail mid-transaction, after the deployment and intent inserts."""
        del args, kwargs
        raise postgres_adoption.SQLAlchemyError("injected failure")

    monkeypatch.setattr(postgres_adoption, "_position_insert", broken_position)
    with pytest.raises(ExecutionStoreError):
        await _start(engine, snapshot)
    assert await _count(engine, deployments) == 0
    assert await _count(engine, order_intents) == 0
