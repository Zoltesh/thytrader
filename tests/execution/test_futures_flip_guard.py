"""Only fill-proven managed excess may be reduced; foreign inventory is never touched."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from tests.execution.test_live_futures_entry import clock
from thytrader.execution.futures_live import (
    FuturesLiveRuntime,
    futures_decisions_deferred,
    live_futures_account_scope,
    live_futures_runtime_scope,
)
from thytrader.execution.futures_position_supervision import enforce_position_check
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentStatus, PositionSide


@pytest.mark.anyio
@pytest.mark.parametrize("projection", ["flat", "smaller", "opposite"])
async def test_fill_proven_excess_is_reduced_then_paused_without_corrupting_book(
    projection: str,
) -> None:
    """A known projection discrepancy permits only the excess, preserving intended inventory."""
    venue = FakeVenue()
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    position = snapshot.position
    assert position is not None
    projected = (
        None
        if projection == "flat"
        else replace(
            position,
            quantity=Decimal("0.01"),
            side=PositionSide.SHORT if projection == "opposite" else position.side,
        )
    )
    await store.save_position(projected, deployment_id=snapshot.deployment.id)
    snapshot = await store.get_deployment(snapshot.deployment.id)
    with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
        for _ in range(2):
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
    assert venue.net == (Decimal(1) if projection == "smaller" else Decimal(0))
    assert snapshot.position == projected
    assert len(snapshot.fills) == 2
    assert snapshot.fills[-1].economics_applied_at is not None
    creates = len(venue.creates)
    with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
        for _ in range(3):
            venue.advance()
            snapshot = await enforce_position_check(
                snapshot,
                account=await venue.read_account(frozenset()),
                broker=venue.broker(),
                store=store,
                now=venue.now,
            )
    assert len(venue.creates) == creates


@pytest.mark.anyio
async def test_mismatch_never_authorizes_replacement_protection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A paused mismatched projection must not rest a fresh order that could open a flip."""
    venue = FakeVenue()
    clock(monkeypatch, venue)
    snapshot = await open_live_book(InMemoryExecutionStore(), venue)
    paused = replace(
        snapshot,
        deployment=replace(
            snapshot.deployment,
            status=DeploymentStatus.PAUSED,
            mismatch_detail="FUTURES_POSITION_MISMATCH: untrusted projection",
        ),
    )
    venue.net = Decimal(0)
    with live_futures_account_scope(await venue.read_account(frozenset())):
        assert futures_decisions_deferred(paused)
    venue.net = Decimal(2)
    with live_futures_account_scope(await venue.read_account(frozenset())):
        assert not futures_decisions_deferred(paused)


@pytest.mark.anyio
async def test_a_definitively_rejected_reduction_is_retried_under_a_new_attempt_key() -> None:
    """A rejection adds no fill, so the fill-keyed identity alone would never be sent again."""
    venue = FakeVenue()
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    await store.save_position(None, deployment_id=snapshot.deployment.id)
    snapshot = await store.get_deployment(snapshot.deployment.id)
    venue.rejected_closes = 1
    with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
        for _ in range(4):
            venue.advance()
            snapshot = await enforce_position_check(
                snapshot,
                account=await venue.read_account(frozenset()),
                broker=venue.broker(),
                store=store,
                now=venue.now,
            )
    assert venue.rejected_closes == 0
    assert venue.net == Decimal(0)
    keys = sorted(intent.idempotency_key or "" for intent in snapshot.intents)
    assert any(key.endswith(":1") for key in keys)


@pytest.mark.anyio
async def test_reduction_attempts_stop_after_the_bound() -> None:
    """Repeated definitive rejections stop after three attempts; the pause and alert remain."""
    venue = FakeVenue()
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    await store.save_position(None, deployment_id=snapshot.deployment.id)
    snapshot = await store.get_deployment(snapshot.deployment.id)
    venue.rejected_closes = 10
    with live_futures_runtime_scope(FuturesLiveRuntime(venue)):
        for _ in range(6):
            venue.advance()
            snapshot = await enforce_position_check(
                snapshot,
                account=await venue.read_account(frozenset()),
                broker=venue.broker(),
                store=store,
                now=venue.now,
            )
    assert venue.rejected_closes == 7
    assert snapshot.deployment.status is DeploymentStatus.PAUSED
