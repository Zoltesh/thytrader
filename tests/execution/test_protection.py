"""Per-product protection status from a deployment snapshot."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from thytrader.execution.models import (
    Deployment,
    DeploymentKind,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Fill,
    IntentPurpose,
    Order,
    OrderIntent,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    PositionSide,
    RuntimePhase,
)
from thytrader.execution.protection import (
    ProtectionStatus,
    book_protection_status,
    working_order_count,
)


def _at() -> datetime:
    """Return a fixed UTC instant."""
    return datetime(2026, 9, 16, 12, tzinfo=UTC)


def _deployment() -> Deployment:
    """Return one paper deployment whose primary product is BTC-USD."""
    now = _at()
    return Deployment(
        id=uuid4(),
        strategy_fingerprint="sha256:" + ("a" * 64),
        strategy_id=uuid4(),
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("10000"),
        phase=RuntimePhase.OPEN,
        created_at=now,
        updated_at=now,
        kind=DeploymentKind.STRATEGY,
        timeframe="1h",
    )


def _position(*, deployment_id: UUID, product_id: str = "ETH-USD") -> Position:
    """Return one open ETH short book."""
    now = _at()
    return Position(
        deployment_id=deployment_id,
        quantity=Decimal("0.5"),
        entry_price=Decimal("3000"),
        stop_price=Decimal("3200"),
        target_price=Decimal("2700"),
        entered_bar=now,
        updated_at=now,
        side=PositionSide.SHORT,
        product_id=product_id,
    )


def test_missing_position_is_flat() -> None:
    """No book means no protection to report."""
    snapshot = DeploymentSnapshot(deployment=_deployment())
    assert (
        book_protection_status(snapshot, product_id="ETH-USD", position=None)
        is ProtectionStatus.FLAT
    )


def test_open_book_without_resting_exit_is_unprotected() -> None:
    """Paper synthetic stops on a filled entry are not venue-visible cover."""
    deployment = _deployment()
    position = _position(deployment_id=deployment.id)
    now = _at()
    entry = Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=uuid4(),
        client_order_id="entry",
        side=OrderSide.SELL,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.5"),
        status=OrderStatus.FILLED,
        created_at=now,
        updated_at=now,
        price=Decimal("3000"),
        filled_quantity=Decimal("0.5"),
        stop_trigger_price=Decimal("3200"),
        take_profit_price=Decimal("2700"),
        product_id="ETH-USD",
    )
    fill = Fill(
        id=uuid4(),
        deployment_id=deployment.id,
        order_id=entry.id,
        venue_fill_id="f1",
        price=Decimal("3000"),
        quantity=Decimal("0.5"),
        fee=Decimal("0"),
        filled_at=now,
    )
    snapshot = DeploymentSnapshot(
        deployment=deployment,
        positions=(position,),
        orders=(entry,),
        fills=(fill,),
    )
    assert (
        book_protection_status(snapshot, product_id="ETH-USD", position=position)
        is ProtectionStatus.UNPROTECTED
    )


def test_working_stop_covers_the_matching_product_only() -> None:
    """A BTC stop does not cover an ETH book."""
    deployment = _deployment()
    position = _position(deployment_id=deployment.id)
    now = _at()
    intent = OrderIntent(
        id=uuid4(),
        deployment_id=deployment.id,
        client_order_id="stop",
        purpose=IntentPurpose.STOP,
        side=OrderSide.BUY,
        kind=OrderKind.MARKETABLE,
        quantity=Decimal("0.5"),
        created_at=now,
        candle_starts_at=now,
        product_id="ETH-USD",
    )
    stop = Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=intent.id,
        client_order_id="stop",
        side=OrderSide.BUY,
        kind=OrderKind.MARKETABLE,
        quantity=Decimal("0.5"),
        status=OrderStatus.OPEN,
        created_at=now,
        updated_at=now,
        product_id="ETH-USD",
    )
    other = Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=uuid4(),
        client_order_id="btc-stop",
        side=OrderSide.SELL,
        kind=OrderKind.MARKETABLE,
        quantity=Decimal("0.01"),
        status=OrderStatus.OPEN,
        created_at=now,
        updated_at=now,
        product_id="BTC-USD",
    )
    snapshot = DeploymentSnapshot(
        deployment=deployment,
        positions=(position,),
        orders=(stop, other),
        intents=(intent,),
    )
    assert (
        book_protection_status(snapshot, product_id="ETH-USD", position=position)
        is ProtectionStatus.COVERED
    )
    assert working_order_count(snapshot.orders) == 2


def test_unknown_protective_order_is_unknown() -> None:
    """An unreconciled protective order is not treated as cover."""
    deployment = _deployment()
    position = _position(deployment_id=deployment.id)
    now = _at()
    intent = OrderIntent(
        id=uuid4(),
        deployment_id=deployment.id,
        client_order_id="stop",
        purpose=IntentPurpose.STOP,
        side=OrderSide.BUY,
        kind=OrderKind.MARKETABLE,
        quantity=Decimal("0.5"),
        created_at=now,
        candle_starts_at=now,
        product_id="ETH-USD",
    )
    stop = Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=intent.id,
        client_order_id="stop",
        side=OrderSide.BUY,
        kind=OrderKind.MARKETABLE,
        quantity=Decimal("0.5"),
        status=OrderStatus.UNKNOWN,
        created_at=now,
        updated_at=now,
        product_id="ETH-USD",
    )
    snapshot = DeploymentSnapshot(
        deployment=deployment,
        positions=(position,),
        orders=(stop,),
        intents=(intent,),
    )
    assert (
        book_protection_status(snapshot, product_id="ETH-USD", position=position)
        is ProtectionStatus.UNKNOWN
    )


def test_attached_child_bracket_covers_when_prices_and_qty_match() -> None:
    """A resting attached child that still matches the book is cover."""
    deployment = _deployment()
    position = _position(deployment_id=deployment.id)
    now = _at()
    entry_intent = OrderIntent(
        id=uuid4(),
        deployment_id=deployment.id,
        client_order_id="entry",
        purpose=IntentPurpose.ENTRY,
        side=OrderSide.SELL,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.5"),
        created_at=now,
        candle_starts_at=now,
        product_id="ETH-USD",
    )
    entry = Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=entry_intent.id,
        client_order_id="entry",
        side=OrderSide.SELL,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=Decimal("0.5"),
        status=OrderStatus.FILLED,
        created_at=now,
        updated_at=now,
        price=Decimal("3000"),
        filled_quantity=Decimal("0.5"),
        stop_trigger_price=Decimal("3200"),
        take_profit_price=Decimal("2700"),
        product_id="ETH-USD",
        attached_child_venue_order_id="child-1",
    )
    child = Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=uuid4(),
        client_order_id="child",
        venue_order_id="child-1",
        side=OrderSide.BUY,
        kind=OrderKind.TRIGGER_BRACKET,
        quantity=Decimal("0.5"),
        status=OrderStatus.OPEN,
        created_at=now,
        updated_at=now,
        stop_trigger_price=Decimal("3200"),
        take_profit_price=Decimal("2700"),
        product_id="ETH-USD",
    )
    fill = Fill(
        id=uuid4(),
        deployment_id=deployment.id,
        order_id=entry.id,
        venue_fill_id="f1",
        price=Decimal("3000"),
        quantity=Decimal("0.5"),
        fee=Decimal("0"),
        filled_at=now,
    )
    snapshot = DeploymentSnapshot(
        deployment=deployment,
        positions=(position,),
        orders=(entry, child),
        fills=(fill,),
        intents=(entry_intent,),
    )
    assert (
        book_protection_status(snapshot, product_id="ETH-USD", position=position)
        is ProtectionStatus.COVERED
    )
