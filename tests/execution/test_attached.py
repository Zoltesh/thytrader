"""F04: unverified attached protection is not coverage."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from thytrader.execution.attached import attached_entry_covers, remaining_quantity
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
    RuntimePhase,
)

_NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)


def _deployment() -> Deployment:
    """Return one live book used as coverage context."""
    return Deployment(
        id=uuid4(),
        strategy_fingerprint="sha256:" + "a" * 64,
        strategy_id=uuid4(),
        product_id="BTC-USD",
        mode=DeploymentMode.LIVE,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("10000"),
        phase=RuntimePhase.OPEN,
        created_at=_NOW,
        updated_at=_NOW,
        timeframe="1h",
    )


def _position(deployment_id: UUID) -> Position:
    """Return the open inventory the attached child must cover."""
    return Position(
        deployment_id=deployment_id,
        quantity=Decimal("0.02"),
        entry_price=Decimal("100"),
        stop_price=Decimal("90"),
        target_price=Decimal("120"),
        entered_bar=_NOW,
        updated_at=_NOW,
        product_id="BTC-USD",
    )


def _entry(*, child_id: str | None, filled_at: datetime = _NOW) -> tuple[Order, Fill]:
    """Return a filled attached entry and the fill that opened the book."""
    order_id = uuid4()
    entry = Order(
        id=order_id,
        deployment_id=uuid4(),
        intent_id=uuid4(),
        client_order_id="entry",
        side=OrderSide.BUY,
        kind=OrderKind.MARKETABLE,
        quantity=Decimal("0.02"),
        status=OrderStatus.FILLED,
        created_at=_NOW,
        updated_at=_NOW,
        price=Decimal("100"),
        filled_quantity=Decimal("0.02"),
        venue_order_id="entry-venue",
        stop_trigger_price=Decimal("90"),
        take_profit_price=Decimal("120"),
        attached_child_venue_order_id=child_id,
        product_id="BTC-USD",
    )
    fill = Fill(
        id=uuid4(),
        deployment_id=entry.deployment_id,
        order_id=order_id,
        venue_fill_id="entry-fill",
        price=Decimal("100"),
        quantity=Decimal("0.02"),
        fee=Decimal("0"),
        filled_at=filled_at,
    )
    return entry, fill


def _child(*, status: OrderStatus, parent: Order) -> Order:
    """Return a tracked attached child for ``parent``."""
    return Order(
        id=uuid4(),
        deployment_id=parent.deployment_id,
        intent_id=parent.intent_id,
        client_order_id="entry:child",
        side=OrderSide.SELL,
        kind=OrderKind.TRIGGER_BRACKET,
        quantity=Decimal("0.02"),
        status=status,
        created_at=_NOW,
        updated_at=_NOW,
        price=Decimal("120"),
        stop_trigger_price=Decimal("90"),
        take_profit_price=Decimal("120"),
        venue_order_id=parent.attached_child_venue_order_id,
        parent_order_id=parent.id,
        product_id="BTC-USD",
    )


def test_missing_child_is_not_coverage() -> None:
    """Parent stop/target geometry without a tracked child is uncovered."""
    deployment = _deployment()
    position = _position(deployment.id)
    entry, fill = _entry(child_id=None)
    snapshot = DeploymentSnapshot(
        deployment=deployment, position=position, orders=(entry,), fills=(fill,)
    )
    assert attached_entry_covers(snapshot, position) is False


def test_open_child_matching_geometry_is_coverage() -> None:
    """A tracked OPEN child matching stop, target, and qty covers the book."""
    deployment = _deployment()
    position = _position(deployment.id)
    entry, fill = _entry(child_id="child-1")
    child = _child(status=OrderStatus.OPEN, parent=entry)
    snapshot = DeploymentSnapshot(
        deployment=deployment,
        position=position,
        orders=(entry, child),
        fills=(fill,),
    )
    assert attached_entry_covers(snapshot, position) is True


def test_canceled_rejected_and_filled_child_are_uncovered() -> None:
    """Canceled, rejected, or already-filled children are not working protection."""
    deployment = _deployment()
    position = _position(deployment.id)
    for status in (OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.FILLED):
        entry, fill = _entry(child_id=f"child-{status.value}")
        child = _child(status=status, parent=entry)
        snapshot = DeploymentSnapshot(
            deployment=deployment,
            position=position,
            orders=(entry, child),
            fills=(fill,),
        )
        assert attached_entry_covers(snapshot, position) is False


def test_remaining_quantity_is_original_minus_filled() -> None:
    """Repricing must use original minus filled, not the prior working quantity."""
    order = Order(
        id=uuid4(),
        deployment_id=uuid4(),
        intent_id=uuid4(),
        client_order_id="partial",
        side=OrderSide.BUY,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("3"),
        status=OrderStatus.OPEN,
        created_at=_NOW,
        updated_at=_NOW,
        price=Decimal("100"),
        filled_quantity=Decimal("1.25"),
    )
    assert remaining_quantity(order) == Decimal("1.75")
