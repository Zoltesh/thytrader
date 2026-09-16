"""Regression gates for atomic fill ledger and fragment-safe pyramiding."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from thytrader.execution.fill_ledger import ingest_fill, replay_unapplied_fills
from thytrader.execution.ids import utc_now, uuid7
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Fill,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    PositionSide,
    RuntimePhase,
)


def _at(hour: int) -> datetime:
    """Return a UTC instant on 2026-01-01."""
    return datetime(2026, 1, 1, hour, tzinfo=UTC)


def _running_snapshot() -> tuple[InMemoryExecutionStore, DeploymentSnapshot, Order]:
    """Build a flat live deployment with one open buy order."""
    now = _at(0)
    deployment_id = uuid4()
    order_id = uuid4()
    deployment = Deployment(
        id=deployment_id,
        strategy_fingerprint="sha256:" + ("a" * 64),
        strategy_id=uuid4(),
        product_id="BTC-USD",
        mode=DeploymentMode.LIVE,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("10000"),
        phase=RuntimePhase.PENDING_ENTRY,
        pending_stop_price=Decimal("90"),
        pending_target_price=Decimal("120"),
        timeframe="1h",
        created_at=now,
        updated_at=now,
    )
    order = Order(
        id=order_id,
        deployment_id=deployment_id,
        intent_id=uuid4(),
        client_order_id="entry",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("1"),
        status=OrderStatus.FILLED,
        created_at=now,
        updated_at=now,
        price=Decimal("100"),
        filled_quantity=Decimal("1"),
        product_id="BTC-USD",
    )
    store = InMemoryExecutionStore()
    return store, DeploymentSnapshot(deployment=deployment, orders=(order,)), order


@pytest.mark.anyio
async def test_replay_unapplied_fill_restores_position_after_crash_boundary() -> None:
    """F01: evidence without economics_applied_at is replayed on restart."""
    store, snapshot, order = _running_snapshot()
    await store.create_deployment(snapshot.deployment)
    await store.save_order(order)
    fill = Fill(
        id=uuid7(utc_now()),
        deployment_id=snapshot.deployment.id,
        order_id=order.id,
        venue_fill_id="venue-fill-1",
        price=Decimal("100"),
        quantity=Decimal("1"),
        fee=Decimal("0.5"),
        filled_at=_at(1),
    )
    await store.save_fill(fill)
    loaded = await store.get_deployment(snapshot.deployment.id)
    replayed = await replay_unapplied_fills(loaded, store=store)
    assert replayed.position is not None
    assert replayed.position.quantity == Decimal("1")
    assert replayed.deployment.cash == Decimal("9899.5")


@pytest.mark.anyio
async def test_ingest_fill_is_idempotent_for_duplicate_observations() -> None:
    """F01/F03: duplicate venue fill ids do not double-apply economics."""
    store, snapshot, order = _running_snapshot()
    await store.create_deployment(snapshot.deployment)
    await store.save_order(order)
    fill = Fill(
        id=uuid7(utc_now()),
        deployment_id=snapshot.deployment.id,
        order_id=order.id,
        venue_fill_id="venue-fill-1",
        price=Decimal("100"),
        quantity=Decimal("1"),
        fee=Decimal("0.5"),
        filled_at=_at(1),
    )
    first = await ingest_fill(snapshot, fill=fill, order=order, store=store)
    second = await ingest_fill(first.snapshot, fill=fill, order=order, store=store)
    assert first.applied is True
    assert second.applied is False
    assert second.snapshot.deployment.cash == Decimal("9899.5")
    assert second.snapshot.position is not None
    assert second.snapshot.position.quantity == Decimal("1")


@pytest.mark.anyio
async def test_scale_in_fragments_do_not_increment_pyramid_add_count() -> None:
    """F28: execution fragments from one order must not exhaust add_count."""
    now = _at(0)
    deployment_id = uuid4()
    order_id = uuid4()
    deployment = Deployment(
        id=deployment_id,
        strategy_fingerprint="sha256:" + ("a" * 64),
        strategy_id=uuid4(),
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        paper_starting_cash=Decimal("10000"),
        cash=Decimal("9900"),
        phase=RuntimePhase.OPEN,
        created_at=now,
        updated_at=now,
    )
    position = Position(
        deployment_id=deployment_id,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        stop_price=Decimal("90"),
        target_price=Decimal("120"),
        entered_bar=_at(1),
        updated_at=now,
        side=PositionSide.LONG,
        product_id="BTC-USD",
        add_count=1,
    )
    order = Order(
        id=order_id,
        deployment_id=deployment_id,
        intent_id=uuid4(),
        client_order_id="entry-frag",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("2"),
        status=OrderStatus.OPEN,
        created_at=now,
        updated_at=now,
        price=Decimal("100"),
        product_id="BTC-USD",
    )
    store = InMemoryExecutionStore()
    await store.create_deployment(deployment)
    await store.save_position(position, deployment_id=deployment_id)
    await store.save_order(order)
    prior_order_id = uuid4()
    prior_fill = Fill(
        id=uuid7(utc_now()),
        deployment_id=deployment_id,
        order_id=prior_order_id,
        venue_fill_id="open",
        price=Decimal("100"),
        quantity=Decimal("1"),
        fee=Decimal("0"),
        filled_at=_at(1),
        economics_applied_at=_at(1),
    )
    await store.save_fill(prior_fill)
    loaded = await store.get_deployment(deployment_id)
    first = Fill(
        id=uuid7(utc_now()),
        deployment_id=deployment_id,
        order_id=order_id,
        venue_fill_id="frag-1",
        price=Decimal("100"),
        quantity=Decimal("0.5"),
        fee=Decimal("0"),
        filled_at=_at(2),
    )
    second = Fill(
        id=uuid7(utc_now()),
        deployment_id=deployment_id,
        order_id=order_id,
        venue_fill_id="frag-2",
        price=Decimal("100"),
        quantity=Decimal("0.5"),
        fee=Decimal("0"),
        filled_at=_at(3),
    )
    after_first = await ingest_fill(loaded, fill=first, order=order, store=store)
    assert after_first.snapshot.position is not None
    assert after_first.snapshot.position.add_count == 1
    after_second = await ingest_fill(after_first.snapshot, fill=second, order=order, store=store)
    assert after_second.snapshot.position is not None
    assert after_second.snapshot.position.add_count == 1
    assert after_second.snapshot.position.quantity == Decimal("2")
