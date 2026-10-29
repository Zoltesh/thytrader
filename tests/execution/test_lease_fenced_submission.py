"""A worker submits orders only for books whose lease it still holds."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import os
from uuid import uuid4

import pytest

from thytrader.execution.leases import RevisionFencedStore, worker_lease_holder
from thytrader.execution.paper import PaperBroker
from thytrader.execution.submit import submit_intent
from thytrader.market_data.models import Candle
from thytrader.trading.ids import utc_now, uuid7
from thytrader.trading.lease_fence import LeaseTakenError
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentStatus,
    ExecutionConflictError,
    IntentPurpose,
    OrderIntent,
    OrderKind,
    OrderSide,
    RuntimePhase,
)

pytestmark = pytest.mark.anyio


async def _book(store: InMemoryExecutionStore) -> Deployment:
    now = utc_now()
    deployment = Deployment(
        id=uuid7(now),
        strategy_fingerprint="sha256:" + ("b" * 64),
        strategy_id=uuid4(),
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("1000"),
        phase=RuntimePhase.OPEN,
        created_at=now,
        updated_at=now,
        kind=DeploymentKind.STRATEGY,
    )
    return await store.create_deployment(deployment)


def _stop_intent(deployment: Deployment) -> OrderIntent:
    now = utc_now()
    return OrderIntent(
        id=uuid7(now),
        deployment_id=deployment.id,
        client_order_id=f"{deployment.id}:stop:{uuid4()}",
        purpose=IntentPurpose.STOP,
        side=OrderSide.SELL,
        kind=OrderKind.STOP_LIMIT,
        quantity=Decimal("0.01"),
        created_at=now,
        candle_starts_at=now,
        stop_trigger_price=Decimal("90"),
        price=Decimal("89"),
        product_id="BTC-USD",
    )


def test_worker_holders_differ_across_containers_sharing_a_pid() -> None:
    """Every container runs the worker as PID 1, so the PID alone cannot name the holder."""
    assert worker_lease_holder() != f"execution-worker:{os.getpid()}"
    assert worker_lease_holder() == worker_lease_holder()
    assert len(worker_lease_holder()) <= 128


async def test_a_pass_whose_lease_another_worker_took_cannot_submit() -> None:
    """A long pass that outlived its lease never sends a protection order for a taken book."""
    store = InMemoryExecutionStore()
    book = await _book(store)
    mine = await store.acquire_worker_lease(
        book.id, holder=worker_lease_holder(), now=utc_now(), ttl=timedelta(seconds=45)
    )
    assert mine is not None
    fenced = RevisionFencedStore(store, book.id, mine.revision)
    await fenced.save_intent(_stop_intent(book))

    taken = await store.acquire_worker_lease(
        book.id,
        holder="execution-worker:other-host:1:feedface",
        now=utc_now() + timedelta(seconds=60),
        ttl=timedelta(seconds=45),
    )
    assert taken is not None
    intent = _stop_intent(book)
    with pytest.raises(ExecutionConflictError, match="Another worker"):
        await fenced.save_intent(intent)
    assert all(item.id != intent.id for item in (await store.get_deployment(book.id)).intents)


async def test_an_unleased_book_and_sibling_intents_are_not_fenced() -> None:
    """Callers without the lease system, and writes for other books, pass through."""
    store = InMemoryExecutionStore()
    book = await _book(store)
    sibling = await _book(store)
    await store.acquire_worker_lease(
        sibling.id,
        holder="execution-worker:other-host:1:feedface",
        now=utc_now(),
        ttl=timedelta(seconds=45),
    )
    fenced = RevisionFencedStore(store, book.id, book.revision)
    await fenced.save_intent(_stop_intent(book))
    await fenced.save_intent(_stop_intent(sibling))


async def test_a_lease_refusal_is_never_answered_with_an_earlier_keyed_order() -> None:
    """A refused keyed entry raises; it does not return the order recorded under its key."""
    store = InMemoryExecutionStore()
    book = await _book(store)
    await store.acquire_worker_lease(
        book.id,
        holder="execution-worker:other-host:1:feedface",
        now=utc_now(),
        ttl=timedelta(seconds=45),
    )
    fenced = RevisionFencedStore(store, book.id, book.revision)
    with pytest.raises(LeaseTakenError):
        await submit_intent(
            store=fenced,
            broker=PaperBroker(),
            deployment_id=book.id,
            product_id="BTC-USD",
            purpose=IntentPurpose.STOP,
            side=OrderSide.SELL,
            kind=OrderKind.STOP_LIMIT,
            quantity=Decimal("0.01"),
            price=Decimal("89"),
            candle=Candle(
                starts_at=utc_now(),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100"),
                volume=Decimal("1"),
            ),
            stop_trigger_price=Decimal("90"),
            idempotency_key="stop-key",
        )
    assert (await store.get_deployment(book.id)).orders == ()
