"""Operator position state: open and protected versus exiting (ADR 0097).

The raw ``phase`` reads ``pending_exit`` as soon as any exit order works, including the
TP/SL bracket (or stop-only protection) resting right after entry. ``position_state`` and
``exit_in_flight`` say which of the two an operator is looking at.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from tests.execution.protection_support import settled_snapshot
from tests.execution.test_loop import _always_entry_strategy, _filled_long
from thytrader.execution import protection
from thytrader.execution.memory import InMemoryExecutionStore
from thytrader.execution.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    LifecycleCommand,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    RuntimePhase,
)
from thytrader.execution.protection import (
    PositionState,
    book_exit_in_flight,
    book_position_state,
    deployment_exit_in_flight,
    deployment_position_state,
)

_NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _reporting_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze reporting recency independently of execution test clocks."""
    monkeypatch.setattr(protection, "utc_now", lambda: _NOW)


def _live(phase: RuntimePhase = RuntimePhase.PENDING_EXIT) -> Deployment:
    """One running live BTC-USD deployment in ``phase``."""
    return Deployment(
        id=uuid4(),
        strategy_fingerprint="sha256:" + ("b" * 64),
        strategy_id=uuid4(),
        product_id="BTC-USD",
        mode=DeploymentMode.LIVE,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("1000"),
        phase=phase,
        created_at=_NOW,
        updated_at=_NOW,
        kind=DeploymentKind.STRATEGY,
        timeframe="1h",
    )


def _long(deployment: Deployment, *, product_id: str = "BTC-USD") -> Position:
    """One open long book."""
    return Position(
        deployment_id=deployment.id,
        quantity=Decimal("0.01"),
        entry_price=Decimal("60000"),
        stop_price=Decimal("58000"),
        target_price=Decimal("64000"),
        entered_bar=_NOW,
        updated_at=_NOW,
        product_id=product_id,
    )


def _order(
    deployment: Deployment,
    *,
    kind: OrderKind,
    side: OrderSide = OrderSide.SELL,
    product_id: str = "BTC-USD",
    status: OrderStatus = OrderStatus.OPEN,
) -> Order:
    """One working order without an intent row (as bounded summary reads carry it)."""
    return Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=uuid4(),
        client_order_id=f"order-{uuid4()}",
        side=side,
        kind=kind,
        quantity=Decimal("0.01"),
        status=status,
        created_at=_NOW,
        updated_at=_NOW,
        price=Decimal("57900") if kind is OrderKind.STOP_LIMIT else Decimal("64000"),
        stop_trigger_price=Decimal("58000"),
        venue_order_id=f"venue-{uuid4()}",
        venue_observed_at=_NOW,
        product_id=product_id,
    )


@pytest.mark.anyio
async def test_paper_book_with_a_resting_take_profit_is_open_protected_not_exiting() -> None:
    """Right after the paper fill the phase reads pending_exit, but nothing is exiting."""
    store = InMemoryExecutionStore()
    filled, _window = await _filled_long(store, _always_entry_strategy())
    assert filled.position is not None
    assert filled.deployment.phase is RuntimePhase.PENDING_EXIT
    assert deployment_position_state(filled) is PositionState.OPEN_PROTECTED
    assert deployment_exit_in_flight(filled) is False
    summary = DeploymentSnapshot(
        deployment=filled.deployment,
        positions=filled.positions,
        orders=tuple(item for item in filled.orders if item.status is OrderStatus.OPEN),
    )
    assert deployment_position_state(summary) is PositionState.OPEN_PROTECTED


@pytest.mark.parametrize("kind", [OrderKind.TRIGGER_BRACKET, OrderKind.STOP_LIMIT])
def test_live_resting_bracket_or_stop_only_protection_is_open_protected(kind: OrderKind) -> None:
    """A resting TP/SL bracket or ADR 0090 stop-limit is protection, not an exit."""
    deployment = _live()
    position = _long(deployment)
    snapshot = DeploymentSnapshot(
        deployment=deployment, positions=(position,), orders=(_order(deployment, kind=kind),)
    )
    assert deployment_position_state(snapshot) is PositionState.OPEN_PROTECTED
    assert not book_exit_in_flight(snapshot, product_id="BTC-USD", position=position)


def test_live_book_with_nothing_resting_is_open_unprotected() -> None:
    """Missing protection is surfaced, never read as protected."""
    deployment = _live(RuntimePhase.OPEN)
    snapshot = DeploymentSnapshot(deployment=deployment, positions=(_long(deployment),))
    assert deployment_position_state(snapshot) is PositionState.OPEN_UNPROTECTED


def test_working_marketable_exit_on_the_closing_side_is_exiting() -> None:
    """A marketable sell against a long book is the exit in flight."""
    deployment = _live()
    position = _long(deployment)
    snapshot = DeploymentSnapshot(
        deployment=deployment,
        positions=(position,),
        orders=(_order(deployment, kind=OrderKind.MARKETABLE),),
    )
    assert book_exit_in_flight(snapshot, product_id="BTC-USD", position=position)
    assert deployment_position_state(snapshot) is PositionState.EXITING
    assert deployment_exit_in_flight(snapshot) is True


def test_opening_side_or_finished_marketable_orders_are_not_an_exit() -> None:
    """A marketable buy on a long book, or a filled sell, is not an exit in flight."""
    deployment = _live()
    position = _long(deployment)
    snapshot = DeploymentSnapshot(
        deployment=deployment,
        positions=(position,),
        orders=(
            _order(deployment, kind=OrderKind.MARKETABLE, side=OrderSide.BUY),
            _order(deployment, kind=OrderKind.MARKETABLE, status=OrderStatus.FILLED),
            _order(deployment, kind=OrderKind.TRIGGER_BRACKET),
        ),
    )
    assert not deployment_exit_in_flight(snapshot)
    # A terminal status without its applied execution cannot certify remaining cover.
    assert deployment_position_state(snapshot) is PositionState.OPEN_UNVERIFIED
    # The retained position/cash in this geometry fixture already reflect the sell.
    assert deployment_position_state(settled_snapshot(snapshot)) is PositionState.OPEN_PROTECTED


def test_signal_exit_marker_and_flatten_are_exiting() -> None:
    """The durable signal-exit marker (ADR 0093) and a flatten request both mean exiting."""
    deployment = _live()
    bracket = _order(deployment, kind=OrderKind.TRIGGER_BRACKET)
    marked = replace(_long(deployment), signal_exit_bar=_NOW)
    snapshot = DeploymentSnapshot(deployment=deployment, positions=(marked,), orders=(bracket,))
    assert deployment_position_state(snapshot) is PositionState.EXITING
    flatten = replace(deployment, lifecycle_command=LifecycleCommand.FLATTEN)
    flattening = DeploymentSnapshot(
        deployment=flatten, positions=(_long(flatten),), orders=(bracket,)
    )
    assert deployment_position_state(flattening) is PositionState.EXITING


def test_flat_and_entering_books() -> None:
    """No inventory is flat, or entering while an entry rests."""
    flat = DeploymentSnapshot(deployment=_live(RuntimePhase.FLAT))
    assert deployment_position_state(flat) is PositionState.FLAT
    entering = DeploymentSnapshot(deployment=_live(RuntimePhase.PENDING_ENTRY))
    assert deployment_position_state(entering) is PositionState.ENTERING
    assert (
        book_position_state(
            entering, product_id="BTC-USD", position=None, phase=RuntimePhase.PENDING_ENTRY
        )
        is PositionState.ENTERING
    )


def test_worst_book_names_the_deployment_state() -> None:
    """One exiting book outranks a protected one; the per-book states stay separate."""
    deployment = _live()
    btc = _long(deployment)
    eth = _long(deployment, product_id="ETH-USD")
    snapshot = DeploymentSnapshot(
        deployment=deployment,
        positions=(btc, eth),
        orders=(
            _order(deployment, kind=OrderKind.TRIGGER_BRACKET),
            _order(deployment, kind=OrderKind.MARKETABLE, product_id="ETH-USD"),
        ),
    )
    assert (
        book_position_state(snapshot, product_id="BTC-USD", position=btc, phase=RuntimePhase.OPEN)
        is PositionState.OPEN_PROTECTED
    )
    assert (
        book_position_state(snapshot, product_id="ETH-USD", position=eth, phase=RuntimePhase.OPEN)
        is PositionState.EXITING
    )
    assert deployment_position_state(snapshot) is PositionState.EXITING
