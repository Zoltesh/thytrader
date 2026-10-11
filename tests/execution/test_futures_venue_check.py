"""I9 accepts only distinct, trustworthy observations, including resting protection."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from tests.execution.test_paper_futures_books import _PERP, _START, _book, _candles, _strategy
from thytrader.audit_events import InMemoryAuditEventStore
from thytrader.execution.audit_scope import execution_audit_scope
from thytrader.execution.futures_live import FuturesLiveRuntime, live_futures_runtime_scope
from thytrader.execution.futures_position_supervision import enforce_position_check
from thytrader.execution.futures_venue_check import PositionConfirmations, compare_position
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.submit import submit_intent
from thytrader.risk.futures_live import FuturesVenueEvidence
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentStatus,
    IntentPurpose,
    OrderKind,
    OrderSide,
    OrderStatus,
)


def evidence(contracts: Decimal = Decimal(1)) -> FuturesVenueEvidence:
    """A complete synthetic CFM read, never a venue acceptance record."""
    return FuturesVenueEvidence(
        observed_at=_START,
        buying_power_usd=Decimal(100),
        initial_margin_usd=Decimal(0),
        positions={_PERP: contracts},
        external_order_products=frozenset(),
        killswitch_enabled=False,
        maintenance={},
        contract_sizes={_PERP: Decimal("0.01")},
    )


@pytest.mark.anyio
async def test_mismatch_requires_two_distinct_valid_samples_and_restart_rechecks() -> None:
    """Reused evidence and unknown samples cannot manufacture confirmation."""
    snapshot = await _book(InMemoryExecutionStore(), _strategy())
    tracker = PositionConfirmations()
    first = compare_position(snapshot, evidence(), now=_START)
    assert first is not None and first.book_contracts == 0 and first.venue_contracts == 1
    assert not tracker.confirm(snapshot.deployment.id, first)
    assert not tracker.confirm(snapshot.deployment.id, first)
    later = replace(evidence(), observed_at=_START + timedelta(seconds=1))
    second = compare_position(snapshot, later, now=later.observed_at)
    assert second is not None
    assert tracker.confirm(snapshot.deployment.id, second)
    assert not PositionConfirmations().confirm(snapshot.deployment.id, second)
    assert not tracker.confirm(snapshot.deployment.id, None)
    assert not tracker.confirm(snapshot.deployment.id, second)


@pytest.mark.anyio
async def test_unknown_stale_future_or_fractional_position_is_not_comparable() -> None:
    """Incomplete or stale account reads never assert equality or a mismatch."""
    snapshot = await _book(InMemoryExecutionStore(), _strategy())
    assert compare_position(snapshot, replace(evidence(), positions=None), now=_START) is None
    assert compare_position(snapshot, evidence(), now=_START + timedelta(seconds=181)) is None
    assert compare_position(snapshot, evidence(), now=_START - timedelta(seconds=1)) is None


@pytest.mark.anyio
async def test_worker_i9_pauses_only_after_two_fresh_mismatches() -> None:
    """The enforcement operation writes the existing durable pause, never foreign reductions."""
    store = InMemoryExecutionStore()
    venue = FakeVenue()
    snapshot = await open_live_book(store, venue)
    venue.net = Decimal(3)
    runtime = FuturesLiveRuntime(venue)
    before = len(venue.creates)
    with live_futures_runtime_scope(runtime):
        snapshot = await enforce_position_check(
            snapshot,
            account=await venue.read_account(frozenset()),
            broker=venue.broker(),
            store=store,
            now=venue.now,
        )
        assert snapshot.deployment.status is DeploymentStatus.RUNNING
        venue.advance()
        snapshot = await enforce_position_check(
            snapshot,
            account=await venue.read_account(frozenset()),
            broker=venue.broker(),
            store=store,
            now=venue.now,
        )
    assert snapshot.deployment.status is DeploymentStatus.PAUSED
    assert "FUTURES_POSITION_MISMATCH" in (snapshot.deployment.mismatch_detail or "")
    assert len(venue.creates) == before


@pytest.mark.anyio
async def test_flat_book_and_venue_cancel_orphan_protection_with_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A known flat account must not retain managed protection capable of opening a flip."""
    venue = FakeVenue()
    monkeypatch.setattr("thytrader.execution.reconcile.utc_now", lambda: venue.now)
    store = InMemoryExecutionStore()
    snapshot = await _book(store, _strategy())
    await store.save_deployment(replace(snapshot.deployment, mode=DeploymentMode.LIVE))
    await submit_intent(
        store=store,
        broker=venue.broker(),
        deployment_id=snapshot.deployment.id,
        product_id=_PERP,
        purpose=IntentPurpose.STOP,
        side=OrderSide.SELL,
        kind=OrderKind.STOP_LIMIT,
        quantity=Decimal("0.01"),
        price=Decimal(89),
        stop_trigger_price=Decimal(90),
        candle=_candles(31)[-1],
    )
    snapshot = await reconcile_open_orders(
        await store.get_deployment(snapshot.deployment.id), broker=venue.broker(), store=store
    )
    audits = InMemoryAuditEventStore()
    with live_futures_runtime_scope(FuturesLiveRuntime(venue)), execution_audit_scope(audits):
        snapshot = await enforce_position_check(
            snapshot,
            account=await venue.read_account(frozenset()),
            broker=venue.broker(),
            store=store,
            now=venue.now,
        )
    assert snapshot.orders[0].status is OrderStatus.CANCELED
    assert any(
        event.action == "futures_orphan_order_cancelled" for event in await audits.list_recent()
    )
