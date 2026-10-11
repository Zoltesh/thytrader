"""Flat confirmation requires complete provenance, including late canonical fill ingestion."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from tests.execution.test_live_futures_entry import clock
from tests.execution_worker.futures_safety_support import closes, protect, step, stop_book
from tests.execution_worker.test_futures_safety_obligations import DelayedReductionVenue
from thytrader.execution.futures_live import (
    FuturesLiveAccount,
    FuturesLiveRuntime,
    live_futures_runtime_scope,
    managed_live_futures,
)
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import LifecycleCommand, runtime_from_deployment

if TYPE_CHECKING:
    from datetime import datetime


class ObservationVenue(FakeVenue):
    """Explicitly replay old account samples or unknown order ownership in a synthetic source."""

    sample_at: datetime | None = None

    async def read_account(
        self,
        managed_client_ids: frozenset[tuple[str, str]],
        product_ids: frozenset[str] = frozenset(),
    ) -> FuturesLiveAccount:
        """Never confuse worker time advancement with a new successful venue observation."""
        account = await super().read_account(managed_client_ids, product_ids)
        if self.sample_at is not None:
            account = replace(account, venue=replace(account.venue, observed_at=self.sample_at))
        return account


@pytest.mark.anyio
@pytest.mark.parametrize("evidence", ["same", "stale", "unknown_ownership"])
async def test_flat_sample_with_insufficient_provenance_does_not_complete(
    monkeypatch: pytest.MonkeyPatch,
    evidence: str,
) -> None:
    """A stopped local-flat row remains supervised until a genuinely later complete read."""
    venue = ObservationVenue()
    clock(monkeypatch, venue)
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    await stop_book(store, snapshot, LifecycleCommand.FLATTEN)
    with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
        flat = await step(store, venue, snapshot.deployment.id)
    assert flat.position is None and managed_live_futures(flat)
    venue.sample_at = venue.now
    venue.advance(181 if evidence == "stale" else 6)
    if evidence == "unknown_ownership":
        venue.sample_at = None
        venue.external = None
    with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
        pending = await step(store, venue, snapshot.deployment.id)
    assert managed_live_futures(pending)
    assert pending.deployment.futures_flat_confirmation_after is not None
    venue.external = frozenset()
    venue.sample_at = None
    with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
        done = await step(store, venue, snapshot.deployment.id)
    assert not managed_live_futures(done) and closes(venue) == 1


@pytest.mark.anyio
async def test_filled_without_canonical_economics_keeps_duty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal order is not a fill; withheld then late-applied economics rearm the bound."""
    venue = DelayedReductionVenue()
    clock(monkeypatch, venue)
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    await stop_book(store, snapshot, LifecycleCommand.FLATTEN)
    with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
        pending = await step(store, venue, snapshot.deployment.id)
        order = pending.orders[-1]
        assert order.venue_order_id is not None
        venue.fill(order.venue_order_id)
        receipt = venue.fills.pop()
        venue.advance()
        missing = await step(store, venue, snapshot.deployment.id)
        assert missing.position is not None and managed_live_futures(missing)
        assert closes(venue) == 1
        venue.fills.append(receipt)
        applied = await step(store, venue, snapshot.deployment.id)
        assert applied.position is None and managed_live_futures(applied)
        assert applied.deployment.futures_flat_confirmation_after == venue.now
        venue.advance()
        confirmed = await step(store, venue, snapshot.deployment.id)
        assert not managed_live_futures(confirmed)


@pytest.mark.anyio
async def test_late_protection_fill_atomically_reconstructs_duty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reconciliation outside the worker cannot erase discovery, even with a product overlay."""
    venue = FakeVenue()
    clock(monkeypatch, venue)
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    snapshot, protection = await protect(store, venue, snapshot)
    await stop_book(store, snapshot, LifecycleCommand.FLATTEN)
    before = await store.get_deployment(snapshot.deployment.id)
    await store.save_deployment(
        before.deployment,
        expected_revision=before.deployment.revision,
        instrument_runtime=runtime_from_deployment(before.deployment, before.deployment.product_id),
    )
    assert before.deployment.futures_flat_confirmation_after is None
    assert protection.venue_order_id is not None
    venue.fill(protection.venue_order_id)
    # This is the last completed transaction before a process dies. No reducer runs.
    flat = await reconcile_open_orders(
        await store.get_deployment(snapshot.deployment.id), broker=venue.broker(), store=store
    )
    assert flat.position is None and flat.deployment.futures_flat_confirmation_after == venue.now
    with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
        same = await step(store, venue, snapshot.deployment.id)
    assert managed_live_futures(same)
    venue.advance()
    with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
        done = await step(store, venue, snapshot.deployment.id)
    assert not managed_live_futures(done) and closes(venue) == 0
