"""Exposure helpers for partial books and stopped residual risk."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    RuntimePhase,
)
from thytrader.risk.exposure import (
    product_exposure,
    risk_bearing_snapshots,
    snapshot_has_residual_exposure,
    working_entry_notional,
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
