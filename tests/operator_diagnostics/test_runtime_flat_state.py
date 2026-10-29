"""The runtime report certifies a settled flat book from one coherent full read (ADR 0153)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from tests.operator_diagnostics.test_deployment_performance import _diagnostics
from tests.operator_diagnostics.test_projection_observability import _NOW
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    ExecutionStoreError,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    RuntimePhase,
)

if TYPE_CHECKING:
    from uuid import UUID

pytestmark = pytest.mark.anyio


class _Store(InMemoryExecutionStore):
    """The in-memory store, optionally failing its one-observation accounting read."""

    def __init__(self, *, fail: bool = False) -> None:
        super().__init__()
        self.fail = fail
        self.accounting_reads: list[UUID] = []

    async def get_accounting_snapshot(self, deployment_id: UUID) -> DeploymentSnapshot:
        self.accounting_reads.append(deployment_id)
        if self.fail:
            raise ExecutionStoreError("Execution storage is unavailable.")
        return await super().get_accounting_snapshot(deployment_id)


async def _book(store: InMemoryExecutionStore, *, phase: RuntimePhase) -> Deployment:
    book = Deployment(
        id=uuid4(),
        strategy_id=uuid4(),
        strategy_fingerprint="sha256:" + "d" * 64,
        product_id="DOGE-USD",
        mode=DeploymentMode.LIVE,
        status=DeploymentStatus.RUNNING,
        phase=phase,
        cash=Decimal("100"),
        initial_equity=Decimal("100"),
        created_at=_NOW,
        updated_at=_NOW,
        timeframe="2h",
        worker_lease_expires_at=_NOW + timedelta(seconds=30),
    )
    return await store.create_deployment(book)


async def _state(store: InMemoryExecutionStore, deployment_id: UUID) -> str | None:
    report = await _diagnostics(execution=store).runtime_report(deployment_id=deployment_id)
    return report.payload.deployments[0].position_state


async def test_a_settled_flat_book_reads_flat() -> None:
    """A flat book whose full snapshot settles reads flat, not open_unverified."""
    store = _Store()
    book = await _book(store, phase=RuntimePhase.FLAT)
    assert await _state(store, book.id) == "flat"
    assert book.id in store.accounting_reads


async def test_an_occupied_book_without_inventory_stays_unverified() -> None:
    """An occupied phase without projected inventory still reads open_unverified."""
    store = _Store()
    book = await _book(store, phase=RuntimePhase.OPEN)
    assert await _state(store, book.id) == "open_unverified"


async def test_a_failed_full_read_never_guesses_flat() -> None:
    """A failed batch read falls back to the summary, which stays unverified."""
    store = _Store(fail=True)
    book = await _book(store, phase=RuntimePhase.FLAT)
    assert await _state(store, book.id) == "open_unverified"


async def test_a_flat_book_with_a_working_order_is_not_certified() -> None:
    """A bracket sibling still open after its leg closed the book keeps the book unverified."""
    store = _Store()
    book = await _book(store, phase=RuntimePhase.FLAT)
    await store.save_order(
        Order(
            id=uuid4(),
            deployment_id=book.id,
            intent_id=uuid4(),
            client_order_id="sibling-stop",
            side=OrderSide.SELL,
            kind=OrderKind.STOP_LIMIT,
            quantity=Decimal("10"),
            status=OrderStatus.UNKNOWN,
            created_at=_NOW,
            updated_at=_NOW,
            stop_trigger_price=Decimal("0.1"),
            product_id="DOGE-USD",
        )
    )
    assert await _state(store, book.id) == "open_unverified"
