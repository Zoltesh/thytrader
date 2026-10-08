"""Exposure helpers for partial books and stopped residual risk."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from thytrader.execution.capital import refresh_performance
from thytrader.trading.exposure import (
    product_exposure,
    risk_bearing_snapshots,
    snapshot_has_residual_exposure,
    working_entry_notional,
)
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    IntentPurpose,
    Order,
    OrderIntent,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    RuntimePhase,
)

_NOW = datetime(2026, 9, 16, 15, tzinfo=UTC)


def _deployment(
    *,
    status: DeploymentStatus = DeploymentStatus.RUNNING,
    phase: RuntimePhase = RuntimePhase.FLAT,
) -> Deployment:
    """One paper deployment for exposure tests."""
    return Deployment(
        id=uuid4(),
        strategy_fingerprint="sha256:" + "a" * 64,
        strategy_id=uuid4(),
        product_id="BTC-USD",
        mode=DeploymentMode.PAPER,
        status=status,
        cash=Decimal("9000"),
        phase=phase,
        created_at=_NOW,
        updated_at=_NOW,
        paper_starting_cash=Decimal("10000"),
    )


def _entry_order(
    *,
    price: Decimal,
    quantity: Decimal,
    filled: Decimal = Decimal("0"),
    kind: OrderKind = OrderKind.POST_ONLY_LIMIT,
) -> Order:
    """One active working entry order."""
    return Order(
        id=uuid4(),
        deployment_id=uuid4(),
        intent_id=uuid4(),
        client_order_id=f"client-{uuid4()}",
        side=OrderSide.BUY,
        kind=kind,
        quantity=quantity,
        status=OrderStatus.OPEN,
        created_at=_NOW,
        updated_at=_NOW,
        price=price,
        filled_quantity=filled,
        product_id="BTC-USD",
    )


def test_working_entry_notional_sums_all_active_non_bracket_orders() -> None:
    """Partial exposure must count every working entry, not only the first."""
    deployment = _deployment()
    orders = (
        _entry_order(price=Decimal("100"), quantity=Decimal("1")),
        _entry_order(price=Decimal("50"), quantity=Decimal("2")),
        _entry_order(
            price=Decimal("200"),
            quantity=Decimal("1"),
            kind=OrderKind.TRIGGER_BRACKET,
        ),
    )
    snapshot = DeploymentSnapshot(deployment=deployment, orders=orders, fills=(), position=None)
    assert working_entry_notional(snapshot, "BTC-USD") == Decimal("200")


def test_product_exposure_includes_position_and_working_entry() -> None:
    """Position cost basis plus working remainder must both count toward product exposure."""
    deployment = _deployment(phase=RuntimePhase.OPEN)
    position = Position(
        deployment_id=deployment.id,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        stop_price=Decimal("90"),
        target_price=Decimal("120"),
        entered_bar=_NOW,
        updated_at=_NOW,
        product_id="BTC-USD",
    )
    working = _entry_order(price=Decimal("100"), quantity=Decimal("0.5"))
    snapshot = DeploymentSnapshot(
        deployment=deployment,
        orders=(working,),
        fills=(),
        position=position,
    )
    assert product_exposure(snapshot, "BTC-USD") == Decimal("150")


def test_snapshot_has_residual_exposure_for_position_working_and_in_market() -> None:
    """Residual exposure covers open inventory, working entries, and in-market phases."""
    flat = DeploymentSnapshot(deployment=_deployment(), orders=(), fills=(), position=None)
    assert snapshot_has_residual_exposure(flat) is False

    positioned = DeploymentSnapshot(
        deployment=_deployment(),
        orders=(),
        fills=(),
        position=Position(
            deployment_id=uuid4(),
            quantity=Decimal("1"),
            entry_price=Decimal("100"),
            stop_price=Decimal("90"),
            target_price=Decimal("120"),
            entered_bar=_NOW,
            updated_at=_NOW,
        ),
    )
    assert snapshot_has_residual_exposure(positioned) is True

    working_only = DeploymentSnapshot(
        deployment=_deployment(),
        orders=(_entry_order(price=Decimal("100"), quantity=Decimal("1")),),
        fills=(),
        position=None,
    )
    assert snapshot_has_residual_exposure(working_only) is True

    pending = DeploymentSnapshot(
        deployment=_deployment(phase=RuntimePhase.PENDING_ENTRY),
        orders=(),
        fills=(),
        position=None,
    )
    assert snapshot_has_residual_exposure(pending) is True


def test_risk_bearing_snapshots_include_stopped_books_with_residual_exposure() -> None:
    """Stopped deployments with open inventory remain in the risk-bearing set."""
    running = DeploymentSnapshot(deployment=_deployment(), orders=(), fills=(), position=None)
    stopped_flat = DeploymentSnapshot(
        deployment=_deployment(status=DeploymentStatus.STOPPED),
        orders=(),
        fills=(),
        position=None,
    )
    stopped_open = DeploymentSnapshot(
        deployment=_deployment(status=DeploymentStatus.STOPPED, phase=RuntimePhase.OPEN),
        orders=(),
        fills=(),
        position=Position(
            deployment_id=uuid4(),
            quantity=Decimal("1"),
            entry_price=Decimal("100"),
            stop_price=Decimal("90"),
            target_price=Decimal("120"),
            entered_bar=_NOW,
            updated_at=_NOW,
        ),
    )
    bearing = risk_bearing_snapshots((running, stopped_flat, stopped_open), DeploymentMode.PAPER)
    assert running in bearing
    assert stopped_flat not in bearing
    assert stopped_open in bearing


@pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL])
@pytest.mark.parametrize("purpose", [None, *IntentPurpose])
def test_exit_intents_do_not_reserve_entry_capital(
    side: OrderSide, purpose: IntentPurpose | None
) -> None:
    """Only entries reserve quote; missing intent evidence stays conservatively occupied."""
    deployment = _deployment(status=DeploymentStatus.STOPPED)
    order = replace(
        _entry_order(price=Decimal("100"), quantity=Decimal("1"), filled=Decimal("0.25")),
        deployment_id=deployment.id,
        side=side,
    )
    intents = (
        ()
        if purpose is None
        else (
            OrderIntent(
                id=order.intent_id,
                deployment_id=deployment.id,
                client_order_id=order.client_order_id,
                purpose=purpose,
                side=side,
                kind=order.kind,
                quantity=order.quantity,
                price=order.price,
                created_at=_NOW,
                candle_starts_at=_NOW,
                product_id="BTC-USD",
            ),
        )
    )
    snapshot = DeploymentSnapshot(deployment=deployment, orders=(order,), intents=intents)
    expected = Decimal("75") if purpose in {None, IntentPurpose.ENTRY} else Decimal("0")
    assert working_entry_notional(snapshot, "BTC-USD") == expected
    assert product_exposure(snapshot, "BTC-USD") == expected
    assert refresh_performance(snapshot, now=_NOW).reserved_buying_power == expected
    assert snapshot_has_residual_exposure(snapshot) is (expected > 0)


def test_protective_exit_does_not_hide_partial_entry_remainder() -> None:
    """An open book still counts its inventory and entry remainder alongside its exit."""
    deployment = _deployment(phase=RuntimePhase.PENDING_EXIT)
    entry = _entry_order(price=Decimal("100"), quantity=Decimal("1.5"), filled=Decimal("1"))
    exit_order = replace(
        _entry_order(price=Decimal("120"), quantity=Decimal("1")), side=OrderSide.SELL
    )
    intent = OrderIntent(
        id=exit_order.intent_id,
        deployment_id=deployment.id,
        client_order_id=exit_order.client_order_id,
        purpose=IntentPurpose.TAKE_PROFIT,
        side=exit_order.side,
        kind=exit_order.kind,
        quantity=exit_order.quantity,
        price=exit_order.price,
        created_at=_NOW,
        candle_starts_at=_NOW,
        product_id="BTC-USD",
    )
    position = Position(
        deployment_id=deployment.id,
        quantity=Decimal("1"),
        entry_price=Decimal("100"),
        stop_price=Decimal("90"),
        target_price=Decimal("120"),
        entered_bar=_NOW,
        updated_at=_NOW,
        product_id="BTC-USD",
    )
    snapshot = DeploymentSnapshot(
        deployment=deployment,
        position=position,
        orders=(entry, exit_order),
        intents=(intent,),
    )
    assert working_entry_notional(snapshot, "BTC-USD") == Decimal("50")
    assert product_exposure(snapshot, "BTC-USD") == Decimal("150")
