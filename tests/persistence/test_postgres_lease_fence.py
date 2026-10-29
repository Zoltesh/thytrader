"""PostgreSQL checks the lease holder in the intent's own transaction (ADR 0153)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
import os
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import SecretStr
import pytest

from tests.persistence.test_postgres_leases import _NOW, _discretionary, _ensure_lease_columns
from thytrader.execution.leases import RevisionFencedStore
from thytrader.persistence.database import create_engine, dispose
from thytrader.persistence.postgres_execution import PostgresExecutionStore
from thytrader.persistence.postgres_execution_statements import _worker_lease_update
from thytrader.persistence.postgres_lease_fence import release_worker_leases
from thytrader.trading.models import (
    ExecutionConflictError,
    IntentPurpose,
    OrderIntent,
    OrderKind,
    OrderSide,
)

if TYPE_CHECKING:
    from uuid import UUID

_TEST_DATABASE_URL = os.getenv("THYTRADER_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        _TEST_DATABASE_URL is None,
        reason="THYTRADER_TEST_DATABASE_URL is required for PostgreSQL integration coverage.",
    ),
]
_TTL = timedelta(seconds=45)


def _stop(deployment_id: UUID) -> OrderIntent:
    return OrderIntent(
        id=uuid4(),
        deployment_id=deployment_id,
        client_order_id=f"{deployment_id}:stop:{uuid4()}",
        purpose=IntentPurpose.STOP,
        side=OrderSide.SELL,
        kind=OrderKind.STOP_LIMIT,
        quantity=Decimal("0.01"),
        created_at=_NOW,
        candle_starts_at=_NOW,
        stop_trigger_price=Decimal("90"),
        price=Decimal("89"),
        product_id="BTC-USD",
    )


async def _intent_ids(store: PostgresExecutionStore, deployment_id: UUID) -> set[UUID]:
    return {item.id for item in (await store.get_deployment(deployment_id)).intents}


async def test_a_taken_book_refuses_the_old_holders_intent() -> None:
    """Worker A's pass cannot record an intent once worker B holds the lease."""
    assert _TEST_DATABASE_URL is not None
    engine = create_engine(SecretStr(_TEST_DATABASE_URL))
    store = PostgresExecutionStore(engine)
    await _ensure_lease_columns(engine)
    book = await store.create_deployment(_discretionary())
    try:
        mine = await store.acquire_worker_lease(book.id, holder="worker-a", now=_NOW, ttl=_TTL)
        assert mine is not None
        fenced = RevisionFencedStore(store, book.id, mine.revision, holder="worker-a")
        kept = await fenced.save_intent(_stop(book.id))
        taken = await store.acquire_worker_lease(
            book.id, holder="worker-b", now=_NOW + timedelta(seconds=50), ttl=_TTL
        )
        assert taken is not None
        refused = _stop(book.id)
        with pytest.raises(ExecutionConflictError, match="Another worker"):
            await fenced.save_intent(refused)
        assert await _intent_ids(store, book.id) == {kept.id}
    finally:
        await dispose(engine)


async def test_the_check_waits_out_a_concurrent_takeover() -> None:
    """An intent insert racing an uncommitted takeover reads the holder it commits under."""
    assert _TEST_DATABASE_URL is not None
    engine = create_engine(SecretStr(_TEST_DATABASE_URL))
    store = PostgresExecutionStore(engine)
    await _ensure_lease_columns(engine)
    book = await store.create_deployment(_discretionary())
    try:
        mine = await store.acquire_worker_lease(book.id, holder="worker-a", now=_NOW, ttl=_TTL)
        assert mine is not None
        fenced = RevisionFencedStore(store, book.id, mine.revision, holder="worker-a")
        async with engine.connect() as takeover:
            transaction = await takeover.begin()
            await takeover.execute(
                _worker_lease_update(
                    book.id, holder="worker-b", now=_NOW + timedelta(seconds=50), ttl=_TTL
                )
            )
            pending = asyncio.ensure_future(fenced.save_intent(_stop(book.id)))
            await asyncio.sleep(0.3)
            assert not pending.done()
            await transaction.commit()
        with pytest.raises(ExecutionConflictError, match="Another worker"):
            await pending
        assert await _intent_ids(store, book.id) == set()
    finally:
        await dispose(engine)


async def test_a_clean_stop_releases_leases_to_the_successor() -> None:
    """A stopped worker's leases expire at once, so a replacement need not wait out the TTL."""
    assert _TEST_DATABASE_URL is not None
    engine = create_engine(SecretStr(_TEST_DATABASE_URL))
    store = PostgresExecutionStore(engine)
    await _ensure_lease_columns(engine)
    book = await store.create_deployment(_discretionary())
    other = await store.create_deployment(_discretionary())
    try:
        assert await store.acquire_worker_lease(book.id, holder="old", now=_NOW, ttl=_TTL)
        assert await store.acquire_worker_lease(other.id, holder="peer", now=_NOW, ttl=_TTL)
        later = _NOW + timedelta(seconds=1)
        await release_worker_leases(engine, holder="old", now=later)
        released = (await store.get_deployment(book.id)).deployment
        assert released.worker_lease_holder is None
        assert await store.acquire_worker_lease(book.id, holder="new", now=later, ttl=_TTL)
        assert await store.acquire_worker_lease(other.id, holder="new", now=later, ttl=_TTL) is None
    finally:
        await dispose(engine)
