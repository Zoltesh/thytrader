"""PostgreSQL inventory adoption: base lock, claims and all-or-nothing writes (ADR 0124)."""

from __future__ import annotations

import asyncio
from decimal import Decimal
import os
from typing import TYPE_CHECKING

from pydantic import SecretStr
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from tests.adoption_support import (
    adopted_book,
    adoption_write,
    balance,
    balance_reader,
    entry_intent,
    live_book,
    working_order,
)
from tests.persistence.test_migration_0048_strategy_root import _alembic, scratch_database
from thytrader.persistence import postgres_adoption
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_adoption import PostgresInventoryAdoptionStore
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.schema import deployments, execution_fills, order_intents
from thytrader.trading.adoption_write import (
    ADOPTION_QUANTITY_UNAVAILABLE,
    AdoptionRefusedError,
)
from thytrader.trading.inventory_claims import ADOPTION_BASE_UNRESOLVED
from thytrader.trading.models import (
    Deployment,
    DeploymentKind,
    ExecutionConflictError,
    ExecutionStoreError,
    IntentPurpose,
    OrderKind,
    OrderSide,
    RuntimePhase,
)
from thytrader.trading.store import InventoryAdoptionStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy import Table
    from sqlalchemy.ext.asyncio import AsyncEngine

    from thytrader.exchanges.models import ExchangeBalance

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


def _book(product_id: str = "DOGE-USD") -> Deployment:
    """A flat live discretionary book (strategy books would need a stored snapshot)."""
    return live_book(product_id=product_id, kind=DeploymentKind.DISCRETIONARY)


async def _count(engine: AsyncEngine, table: Table) -> int:
    """Count the rows of one table."""
    async with engine.connect() as connection:
        counted = await connection.scalar(select(func.count()).select_from(table))
    return int(counted or 0)


async def test_concurrent_adoptions_on_one_base_let_exactly_one_through(
    engine: AsyncEngine,
) -> None:
    """Two books each ask for 80 of 100 DOGE at once; the exclusive lock admits one."""
    execution = PostgresExecutionStore(engine)
    adoption = PostgresInventoryAdoptionStore(engine)
    assert isinstance(adoption, InventoryAdoptionStore)
    first = await execution.create_deployment(_book())
    second = await execution.create_deployment(_book())

    async def slow_venue() -> tuple[ExchangeBalance, ...]:
        """Hold the lock long enough for the other adoption to queue behind it."""
        await asyncio.sleep(0.2)
        return (balance("DOGE", "100"),)

    results = await asyncio.gather(
        adoption.adopt_inventory(
            adoption_write(first, quantity=Decimal(80)), read_balances=slow_venue
        ),
        adoption.adopt_inventory(
            adoption_write(second, quantity=Decimal(80)), read_balances=slow_venue
        ),
        return_exceptions=True,
    )
    refused = [item for item in results if isinstance(item, AdoptionRefusedError)]
    assert len(refused) == 1 and refused[0].code == ADOPTION_QUANTITY_UNAVAILABLE, results
    assert await _count(engine, execution_fills) == 1


async def test_a_live_entry_intent_waits_for_the_adoption_and_is_then_claimed(
    engine: AsyncEngine,
) -> None:
    """The shared lock keeps an entry intent out of an adoption's critical section."""
    execution = PostgresExecutionStore(engine)
    adoption = PostgresInventoryAdoptionStore(engine)
    book = await execution.create_deployment(_book())
    trader = await execution.create_deployment(_book())
    called = asyncio.Event()
    release = asyncio.Event()

    async def held_venue() -> tuple[ExchangeBalance, ...]:
        """Signal, then wait until the test has started the entry."""
        called.set()
        await release.wait()
        return (balance("DOGE", "100"),)

    adopting = asyncio.create_task(
        adoption.adopt_inventory(
            adoption_write(book, quantity=Decimal(70)), read_balances=held_venue
        )
    )
    await called.wait()
    entering = asyncio.create_task(execution.save_intent(entry_intent(trader)))
    await asyncio.sleep(0.3)
    assert not entering.done()
    release.set()
    await adopting
    await entering
    other = await execution.create_deployment(_book(product_id="DOGE-USDC"))
    with pytest.raises(AdoptionRefusedError) as refused:
        await adoption.adopt_inventory(
            adoption_write(other, quantity=Decimal(1)),
            read_balances=balance_reader(balance("DOGE", "100")),
        )
    assert refused.value.code == ADOPTION_QUANTITY_UNAVAILABLE  # 100 - 70 - pending 30


async def test_a_protected_long_is_not_subtracted_twice(engine: AsyncEngine) -> None:
    """Available already excludes the protective sell's hold; only the long is claimed."""
    execution = PostgresExecutionStore(engine)
    adoption = PostgresInventoryAdoptionStore(engine)
    owner = await adopted_book(execution, book=_book())
    bracket = entry_intent(
        owner.deployment, side=OrderSide.SELL, quantity=Decimal(100), purpose=IntentPurpose.BRACKET
    )
    await execution.save_intent(bracket)
    await execution.save_order(working_order(bracket, kind=OrderKind.TRIGGER_BRACKET))
    venue = balance_reader(balance("DOGE", "50", hold="100"))
    newcomer = await execution.create_deployment(_book())
    commit = await adoption.adopt_inventory(
        adoption_write(newcomer, quantity=None), read_balances=venue
    )
    assert commit.records.order.quantity == Decimal(50)
    assert commit.availability.claims.managed_long == Decimal(100)
    assert commit.snapshot.deployment.phase is RuntimePhase.OPEN


async def test_a_missing_balance_is_denied_and_writes_nothing(engine: AsyncEngine) -> None:
    """No DOGE row is unknown, not zero."""
    execution = PostgresExecutionStore(engine)
    adoption = PostgresInventoryAdoptionStore(engine)
    book = await execution.create_deployment(_book())
    with pytest.raises(AdoptionRefusedError) as refused:
        await adoption.adopt_inventory(
            adoption_write(book), read_balances=balance_reader(balance("USD", "1000"))
        )
    assert refused.value.code == ADOPTION_BASE_UNRESOLVED
    snapshot = await execution.get_deployment(book.id)
    assert snapshot.intents == () and snapshot.position is None
    assert snapshot.deployment.revision == book.revision


async def test_a_mid_transaction_failure_rolls_back_the_book_and_every_record(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure after the book, intent, order and fill inserts leaves no row behind."""
    adoption = PostgresInventoryAdoptionStore(engine)
    book = _book()

    def broken_position(*args: object, **kwargs: object) -> object:
        """Fail like the database dropping the connection mid-write."""
        del args, kwargs
        raise SQLAlchemyError("injected failure")

    monkeypatch.setattr(postgres_adoption, "_position_insert", broken_position)
    with pytest.raises(ExecutionStoreError):
        await adoption.adopt_inventory(
            adoption_write(book, new=True), read_balances=balance_reader(balance("DOGE", "100"))
        )
    assert await _count(engine, deployments) == 0
    assert await _count(engine, order_intents) == 0
    assert await _count(engine, execution_fills) == 0
    monkeypatch.undo()
    commit = await adoption.adopt_inventory(
        adoption_write(book, new=True), read_balances=balance_reader(balance("DOGE", "100"))
    )
    assert commit.snapshot.position is not None and commit.snapshot.deployment.revision == 1


async def test_a_reused_idempotency_key_rolls_back_a_new_book(engine: AsyncEngine) -> None:
    """The duplicate is caught at the intent insert, after the new book was inserted."""
    execution = PostgresExecutionStore(engine)
    adoption = PostgresInventoryAdoptionStore(engine)
    reader = balance_reader(balance("DOGE", "500"))
    first = await execution.create_deployment(_book())
    await adoption.adopt_inventory(
        adoption_write(first, idempotency_key="adopt-once"), read_balances=reader
    )
    late = _book(product_id="DOGE-USDC")
    with pytest.raises(ExecutionConflictError, match="idempotency"):
        await adoption.adopt_inventory(
            adoption_write(late, new=True, idempotency_key="adopt-once"), read_balances=reader
        )
    with pytest.raises(ExecutionStoreError, match="not found"):
        await execution.get_deployment(late.id)
