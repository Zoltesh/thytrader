"""Crash every durable safety transition through the actual fenced futures worker."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from tests.execution.test_live_futures_entry import clock
from tests.execution.test_paper_futures_books import _binding, _Observations
from tests.execution_worker.futures_safety_support import closes, protect, step, stop_book
from thytrader.execution.futures_live import (
    FuturesLiveRuntime,
    live_futures_runtime_scope,
    managed_live_futures,
)
from thytrader.execution.futures_paper import FuturesRuntime, futures_runtime_scope
from thytrader.trading.futures_book import InMemoryFuturesContractStore
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    Deployment,
    DeploymentSnapshot,
    Fill,
    InstrumentRuntime,
    LifecycleCommand,
    Order,
    OrderIntent,
    OrderKind,
)

if TYPE_CHECKING:
    from uuid import UUID


class ProcessLoss(BaseException):
    """A process interruption, deliberately not caught as a venue transport error."""


class CrashStore(InMemoryExecutionStore):
    """Real memory transactions with interruption only after named committed boundaries."""

    boundary: str | None = None

    def trip(self, boundary: str) -> None:
        """Crash once, preserving already-committed data for a new worker runtime."""
        if self.boundary == boundary:
            self.boundary = None
            raise ProcessLoss(boundary)

    async def save_deployment(
        self,
        deployment: Deployment,
        *,
        expected_revision: int | None = None,
        instrument_runtime: InstrumentRuntime | None = None,
    ) -> Deployment:
        """Interrupt only the first commit establishing or clearing the obligation."""
        before = await self.get_deployment(deployment.id)
        result = await super().save_deployment(
            deployment, expected_revision=expected_revision, instrument_runtime=instrument_runtime
        )
        if deployment.futures_derisk_pending and not before.deployment.futures_derisk_pending:
            self.trip("derisk")
        if (
            deployment.futures_flat_confirmation_after is not None
            and before.deployment.futures_flat_confirmation_after is None
        ):
            self.trip("flat_pending")
        if (
            deployment.futures_flat_confirmation_after is None
            and before.deployment.futures_flat_confirmation_after is not None
        ):
            self.trip("flat_confirmed")
        return result

    async def save_intent(self, intent: OrderIntent) -> OrderIntent:
        """Interrupt intent persistence before local order creation and POST."""
        result = await super().save_intent(intent)
        if intent.kind is OrderKind.MARKETABLE:
            self.trip("intent")
        return result

    async def save_order(self, order: Order) -> Order:
        """Interrupt the pre-POST local order anchor or the accepted order acknowledgement."""
        result = await super().save_order(order)
        if order.kind is OrderKind.MARKETABLE:
            self.trip("order" if order.venue_order_id is None else "ack")
        return result

    async def apply_fill_transaction(
        self,
        deployment_id: UUID,
        *,
        fill: Fill,
        order: Order,
        cooldown_bars: int = 0,
        timeframe: str | None = None,
    ) -> tuple[bool, DeploymentSnapshot]:
        """Interrupt immediately after atomic close fill commit, before any later latch write."""
        result = await super().apply_fill_transaction(
            deployment_id, fill=fill, order=order, cooldown_bars=cooldown_bars, timeframe=timeframe
        )
        if order.kind is OrderKind.MARKETABLE:
            self.trip("fill")
        return result


@pytest.mark.anyio
@pytest.mark.parametrize(
    "boundary", ["derisk", "flat_pending", "intent", "order", "ack", "fill", "flat_confirmed"]
)
async def test_restart_preserves_safety_duties_and_never_resends(
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    """R1/R2: every committed boundary remains discoverable after process-local state is lost."""
    venue = FakeVenue()
    clock(monkeypatch, venue)
    store = CrashStore()
    snapshot = await open_live_book(store, venue)
    snapshot, _ = await protect(store, venue, snapshot)
    await stop_book(store, snapshot, LifecycleCommand.FLATTEN)
    bindings = InMemoryFuturesContractStore()
    await bindings.bind_contract(_binding(snapshot))
    venue.margin = venue.threshold / 2
    store.boundary = boundary
    with futures_runtime_scope(FuturesRuntime(bindings, _Observations(()))):
        with pytest.raises(ProcessLoss), live_futures_runtime_scope(FuturesLiveRuntime(venue)):
            for _ in range(3):
                venue.advance()
                await step(store, venue, snapshot.deployment.id)
        crashed = await store.get_deployment(snapshot.deployment.id)
        if boundary != "flat_confirmed":
            assert managed_live_futures(crashed)
        # Recover margin; restart must not abandon an already committed de-risk request.
        venue.margin = venue.threshold * 10
        for _ in range(4):
            venue.advance()
            with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
                current = await step(store, venue, snapshot.deployment.id)
        if boundary in {"intent", "order"}:
            # Pre-POST loss is ambiguous by design: keep the original client id; never resend.
            assert closes(venue) == 0
            assert current.position is not None
            assert current.deployment.futures_flat_confirmation_after is not None
        else:
            assert closes(venue) == 1
            assert current.position is None and venue.net == 0
            assert current.deployment.futures_flat_confirmation_after is None
            assert not current.deployment.futures_derisk_pending
