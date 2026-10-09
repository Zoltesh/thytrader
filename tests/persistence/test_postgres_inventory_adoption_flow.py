"""PostgreSQL end to end: protect and sell held coins, then the worker sells (ADR 0124)."""

from __future__ import annotations

from decimal import Decimal
import os
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from pydantic import SecretStr
import pytest

from tests.adoption_support import balance, balance_reader
from tests.execution.test_inventory_adoption_service import _protect, _sell, _Venue
from tests.persistence.test_migration_0048_strategy_root import _alembic, scratch_database
from thytrader.api.dependencies import get_inventory_adoption_store
from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.execution.adoption_discretionary import adopt_held_inventory
from thytrader.execution.audit_scope import execution_audit_scope
from thytrader.execution.closed_windows import _closed_window_for
from thytrader.execution.paper import PaperBroker
from thytrader.execution.stopped import supervise_stopped_deployment
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.service import MarketDataService
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_adoption import PostgresInventoryAdoptionStore
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.postgres_memory import PostgresExperientialMemoryStore
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.risk.store import InMemoryRiskPolicyStore
from thytrader.trading.models import (
    DeploymentStatus,
    IntentPurpose,
    LifecycleCommand,
    OrderKind,
    OrderSide,
    RuntimePhase,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.execution.adoption import AdoptionRequest
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


async def _adopt(
    engine: AsyncEngine, request: AdoptionRequest, venue: _Venue
) -> DeploymentSnapshot:
    """One adoption through the service with PostgreSQL stores and 0.5 SOL held."""
    risk = InMemoryRiskPolicyStore()
    await risk.publish(
        compiled_default_risk_policy().model_copy(
            update={
                "version": 2,
                "max_portfolio_exposure_fraction": "1",
                "per_product_max_exposure_fraction": "1",
            }
        )
    )
    with execution_audit_scope(InMemoryAuditEventStore()):
        return await adopt_held_inventory(
            request,
            store=PostgresExecutionStore(engine),
            adoption_store=PostgresInventoryAdoptionStore(engine),
            market_data=MarketDataService(DemoMarketData()),
            broker=venue,
            read_balances=balance_reader(balance("SOL", "0.5"), balance("USD", "100000")),
            live_quote_cash=Decimal(100000),
            risk_store=risk,
            memory_store=PostgresExperientialMemoryStore(engine),
        )


async def test_the_api_dependency_pairs_postgres_with_its_adoption_store(
    engine: AsyncEngine,
) -> None:
    """The route gets the PostgreSQL adoption store on the execution store's engine."""
    state = SimpleNamespace(execution_store=PostgresExecutionStore(engine))
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    # A namespace stands in for the Request: the dependency reads only app.state.
    store = get_inventory_adoption_store(cast("Request", request))
    assert isinstance(store, PostgresInventoryAdoptionStore)


async def test_protect_commits_the_adoption_and_the_bracket_and_replays(
    engine: AsyncEngine,
) -> None:
    """The adoption rows, the position and the OCO survive a reload; the key replays."""
    venue = _Venue()
    adopted = await _adopt(engine, _protect(), venue)
    reloaded = await PostgresExecutionStore(engine).get_deployment(adopted.deployment.id)
    assert reloaded.deployment.status is DeploymentStatus.RUNNING
    assert reloaded.position is not None and reloaded.position.quantity == Decimal("0.3")
    assert sorted(intent.purpose.value for intent in reloaded.intents) == sorted(
        (IntentPurpose.ADOPTION.value, IntentPurpose.BRACKET.value)
    )
    assert venue.protective() == [OrderKind.TRIGGER_BRACKET]
    replay = await _adopt(engine, _protect(), venue)
    assert replay.deployment.id == adopted.deployment.id and len(venue.placed) == 1
    records = await PostgresExperientialMemoryStore(engine).list_trade_reasons(
        deployment_id=adopted.deployment.id
    )
    assert "adoption" in {record.purpose for record in records}


async def test_sell_is_sold_flat_by_the_worker_without_protection(engine: AsyncEngine) -> None:
    """STOPPED+FLATTEN book in PostgreSQL; the stopped-book flatten sells it exactly once."""
    venue = _Venue()
    adopted = await _adopt(engine, _sell(), venue)
    assert adopted.deployment.lifecycle_command is LifecycleCommand.FLATTEN
    assert venue.placed == []
    execution = PostgresExecutionStore(engine)
    for _cycle in range(3):
        await supervise_stopped_deployment(
            await execution.get_deployment(adopted.deployment.id),
            strategy=None,
            store=execution,
            market_data=MarketDataService(DemoMarketData()),
            paper_broker=PaperBroker(),
            live_broker=venue,
            load_closed_window=_closed_window_for,
        )
    assert venue.placed == [(OrderKind.MARKETABLE, OrderSide.SELL, Decimal("0.5"))]
    final = await execution.get_deployment(adopted.deployment.id)
    assert final.position is None and final.deployment.phase is RuntimePhase.FLAT
    assert final.deployment.status is DeploymentStatus.STOPPED
    assert all(fill.economics_applied_at is not None for fill in final.fills)
