"""A margin exit is durable across cancel uncertainty and entry disarm."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tests.execution.futures_live_fixture import FakeVenue
from tests.execution.test_futures_reduction import open_live_book
from tests.execution.test_live_futures_entry import clock
from tests.execution.test_paper_futures_books import _PERP, _candles, _state
from thytrader.execution.exit_guards import PROTECTIVE_REJECTED_PREFIX
from thytrader.execution.futures_live_liquidation import supervise_live_futures
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.submit import submit_intent
from thytrader.risk.futures_policy import FuturesRiskPolicy
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.trading.entry_latch import clear_entry_inhibition_cache, remember_entry_inhibition
from thytrader.trading.memory import InMemoryExecutionStore
from thytrader.trading.models import DeploymentStatus, IntentPurpose, OrderKind, OrderSide


@pytest.mark.anyio
async def test_derisk_cancel_uncertainty_persists_without_duplicate_or_reentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resting protection is confirmed canceled first; recovered margin cannot erase the exit."""
    venue = FakeVenue()
    clock(monkeypatch, venue)
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    await submit_intent(
        store=store,
        broker=venue.broker(),
        deployment_id=snapshot.deployment.id,
        product_id=_PERP,
        purpose=IntentPurpose.STOP,
        side=OrderSide.SELL,
        kind=OrderKind.STOP_LIMIT,
        quantity=Decimal("0.02"),
        price=Decimal(89),
        stop_trigger_price=Decimal(90),
        candle=_candles(31)[-1],
    )
    snapshot = await reconcile_open_orders(
        await store.get_deployment(snapshot.deployment.id), broker=venue.broker(), store=store
    )
    venue.margin = Decimal(1)
    venue.cancel_confirmed = False
    policy = compiled_default_risk_policy().model_copy(
        update={"futures": FuturesRiskPolicy(live_enabled=False, live_derisk_margin_ratio="1.5")}
    )
    remember_entry_inhibition({"live": True, "paper": False})
    try:
        pending = await supervise_live_futures(
            snapshot,
            state=_state(snapshot),
            account=await venue.read_account(frozenset()),
            policy=policy,
            broker=venue.broker(),
            store=store,
            now=venue.now,
        )
        assert not any(intent.purpose is IntentPurpose.LIQUIDATION for intent in pending.intents)
        assert (pending.deployment.mismatch_detail or "").startswith("FUTURES_DERISK_PENDING")
        venue.cancel_confirmed = True
        venue.margin = Decimal(100)
        venue.advance()
        closed = await supervise_live_futures(
            pending,
            state=_state(pending),
            account=await venue.read_account(frozenset()),
            policy=policy,
            broker=venue.broker(),
            store=store,
            now=venue.now,
        )
    finally:
        clear_entry_inhibition_cache()
    assert closed.position is None
    assert closed.deployment.status is DeploymentStatus.PAUSED
    assert "FUTURES_DERISK_COMPLETED" in (closed.deployment.mismatch_detail or "")
    assert sum(intent.purpose is IntentPurpose.LIQUIDATION for intent in closed.intents) == 1


@pytest.mark.anyio
async def test_rejected_derisk_closes_latch_after_five_without_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The de-risk wrapper keeps the rejection pause, so the five-rejection latch holds."""
    venue = FakeVenue()
    clock(monkeypatch, venue)
    store = InMemoryExecutionStore()
    snapshot = await open_live_book(store, venue)
    venue.margin = Decimal(1)
    venue.rejected_closes = 10
    policy = compiled_default_risk_policy().model_copy(
        update={"futures": FuturesRiskPolicy(live_enabled=False, live_derisk_margin_ratio="1.5")}
    )
    for _ in range(7):
        snapshot = await supervise_live_futures(
            await store.get_deployment(snapshot.deployment.id),
            state=_state(snapshot),
            account=await venue.read_account(frozenset()),
            policy=policy,
            broker=venue.broker(),
            store=store,
            now=venue.now,
        )
        venue.advance(31 * 60)
    closes = [call for call in venue.calls if call[1].endswith("/close_position")]
    assert len(closes) == 5
    assert snapshot.position is not None
    assert snapshot.deployment.futures_derisk_pending
    assert snapshot.deployment.status is DeploymentStatus.PAUSED
    assert (snapshot.deployment.mismatch_detail or "").startswith(PROTECTIVE_REJECTED_PREFIX)
