"""Quantitative protection evidence: geometry, quantity, and no false cover (ADR 0112)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from thytrader.api.routes.deployments import _position_response
from thytrader.execution import protection
from thytrader.execution.attached import attached_entry_covers
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
    ProtectionEvidence,
    ProtectionStatus,
    book_protection_evidence,
    book_protection_status,
)
from thytrader.portfolios.runtime_views import open_books

_NOW = datetime(2026, 9, 16, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _reporting_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep row recency tests deterministic without changing execution clocks."""
    monkeypatch.setattr(protection, "utc_now", lambda: _NOW)


def _deployment(
    *, mode: DeploymentMode = DeploymentMode.LIVE, product_id: str = "BTC-USD"
) -> Deployment:
    """Return one running deployment."""
    return Deployment(
        id=uuid4(),
        strategy_fingerprint="sha256:" + ("b" * 64),
        strategy_id=uuid4(),
        product_id=product_id,
        mode=mode,
        status=DeploymentStatus.RUNNING,
        cash=Decimal("10000"),
        phase=RuntimePhase.OPEN,
        created_at=_NOW,
        updated_at=_NOW,
        kind=DeploymentKind.STRATEGY,
        timeframe="1h",
    )


def _position(
    deployment: Deployment,
    *,
    product_id: str,
    side: PositionSide,
    quantity: str = "0.5",
    entry: str = "3000",
    stop: str = "3200",
    target: str | None = "2700",
) -> Position:
    """Return one open book with explicit stop geometry."""
    return Position(
        deployment_id=deployment.id,
        quantity=Decimal(quantity),
        entry_price=Decimal(entry),
        stop_price=Decimal(stop),
        target_price=None if target is None else Decimal(target),
        entered_bar=_NOW,
        updated_at=_NOW,
        side=side,
        product_id=product_id,
    )


def _order(
    deployment: Deployment,
    position: Position,
    *,
    kind: OrderKind = OrderKind.TRIGGER_BRACKET,
    status: OrderStatus = OrderStatus.OPEN,
    side: OrderSide | None = None,
    quantity: str | None = None,
    filled: str = "0",
    stop: str | None = None,
    target: str | None = None,
    price: str | None = None,
    venue_order_id: str | None = "child-1",
    parent_order_id: UUID | None = None,
    intent_id: UUID | None = None,
    updated_at: datetime | None = None,
    venue_observed_at: datetime | None = _NOW,
) -> Order:
    """Return one order. Stop and target are set only when the caller passes them."""
    closing = OrderSide.BUY if position.side is PositionSide.SHORT else OrderSide.SELL
    return Order(
        id=uuid4(),
        deployment_id=deployment.id,
        intent_id=uuid4() if intent_id is None else intent_id,
        client_order_id=f"order-{uuid4()}",
        side=closing if side is None else side,
        kind=kind,
        quantity=position.quantity if quantity is None else Decimal(quantity),
        status=status,
        created_at=_NOW,
        updated_at=_NOW if updated_at is None else updated_at,
        venue_observed_at=venue_observed_at,
        price=None if price is None else Decimal(price),
        filled_quantity=Decimal(filled),
        stop_trigger_price=None if stop is None else Decimal(stop),
        take_profit_price=None if target is None else Decimal(target),
        venue_order_id=venue_order_id,
        product_id=position.product_id,
        parent_order_id=parent_order_id,
    )


def _evidence(snapshot: DeploymentSnapshot, position: Position) -> ProtectionEvidence:
    """Evidence for ``position`` on ``snapshot``."""
    return book_protection_evidence(snapshot, product_id=position.product_id, position=position)


def test_matching_eth_and_ada_brackets_stay_covered() -> None:
    """Live ETH and ADA books with matching full brackets remain covered."""
    deployment = _deployment()
    eth = _position(deployment, product_id="ETH-USD", side=PositionSide.SHORT)
    ada = _position(
        deployment,
        product_id="ADA-USD",
        side=PositionSide.LONG,
        quantity="20",
        entry="0.40",
        stop="0.36",
        target="0.48",
    )
    eth_stop = _order(
        deployment,
        eth,
        stop="3200",
        target="2700",
        price="2700",
        venue_order_id="eth-bracket",
    )
    ada_stop = _order(
        deployment,
        ada,
        stop="0.36",
        target="0.48",
        price="0.48",
        venue_order_id="ada-bracket",
    )
    snapshot = DeploymentSnapshot(
        deployment=deployment, positions=(eth, ada), orders=(eth_stop, ada_stop)
    )
    for position in (eth, ada):
        evidence = _evidence(snapshot, position)
        assert evidence.status is ProtectionStatus.COVERED
        assert evidence.mechanism.value == "venue"
        assert evidence.venue_resting is True
        assert evidence.worker_dependent is False
        assert evidence.stop_side_valid is True
        assert evidence.stop_geometry_valid is True
        assert evidence.covered_quantity == position.quantity
        assert evidence.uncovered_quantity == Decimal(0)
        assert evidence.observed_at == _NOW
        assert evidence.verified_at == _NOW
        assert evidence.observation_source == "venue_order_state"
        assert evidence.freshness == "recent_venue"
        assert "local_observation_only" not in evidence.reasons
        assert "venue_stop_resting" in evidence.reasons


def test_post_fill_bracket_uses_limit_price_as_target_on_a_summary_read() -> None:
    """A live OCO records the target on price, not take_profit_price, and still covers."""
    deployment = _deployment(product_id="ETH-USD")
    position = _position(
        deployment,
        product_id="ETH-USD",
        side=PositionSide.LONG,
        quantity="1",
        entry="3000",
        stop="2800",
        target="3400",
    )
    bracket = _order(
        deployment,
        position,
        stop="2800",
        price="3400",
        venue_order_id="oco-1",
    )
    summary = DeploymentSnapshot(deployment=deployment, positions=(position,), orders=(bracket,))
    evidence = _evidence(summary, position)
    assert evidence.status is ProtectionStatus.COVERED
    assert evidence.required_quantity == Decimal("1")
    assert evidence.covered_quantity == Decimal("1")


def test_stop_only_limit_covers_without_a_take_profit() -> None:
    """ADR 0090: an open stop-limit at the working stop covers a no-target book."""
    deployment = _deployment(product_id="BTC-USD")
    position = _position(
        deployment,
        product_id="BTC-USD",
        side=PositionSide.LONG,
        quantity="0.01",
        entry="100",
        stop="90",
        target=None,
    )
    stop = _order(
        deployment,
        position,
        kind=OrderKind.STOP_LIMIT,
        stop="90",
        price="85.50",
        venue_order_id="stop-1",
    )
    snapshot = DeploymentSnapshot(deployment=deployment, positions=(position,), orders=(stop,))
    evidence = _evidence(snapshot, position)
    assert evidence.status is ProtectionStatus.COVERED
    assert evidence.stop_geometry_valid is True
    assert "venue_stop_resting" in evidence.reasons


def test_take_profit_only_is_unprotected_on_full_and_summary_reads() -> None:
    """A closing take-profit does not count as a stop on either read shape."""
    deployment = _deployment(product_id="ETH-USD")
    position = _position(
        deployment,
        product_id="ETH-USD",
        side=PositionSide.LONG,
        entry="3000",
        stop="2800",
        target="3400",
    )
    intent = OrderIntent(
        id=uuid4(),
        deployment_id=deployment.id,
        client_order_id="tp",
        purpose=IntentPurpose.TAKE_PROFIT,
        side=OrderSide.SELL,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=position.quantity,
        created_at=_NOW,
        candle_starts_at=_NOW,
        price=Decimal("3400"),
        product_id="ETH-USD",
    )
    take_profit = _order(
        deployment,
        position,
        kind=OrderKind.POST_ONLY_LIMIT,
        price="3400",
        venue_order_id="tp-1",
        intent_id=intent.id,
    )
    summary = DeploymentSnapshot(
        deployment=deployment, positions=(position,), orders=(take_profit,)
    )
    full = DeploymentSnapshot(
        deployment=deployment, positions=(position,), orders=(take_profit,), intents=(intent,)
    )
    for snapshot in (summary, full):
        evidence = _evidence(snapshot, position)
        assert evidence.status is ProtectionStatus.UNPROTECTED
        assert evidence.covered_quantity == Decimal(0)
        assert evidence.uncovered_quantity == position.quantity
        assert evidence.venue_resting is False
        assert "take_profit_only" in evidence.reasons
        assert "no_resting_stop" in evidence.reasons


def test_pending_and_unknown_matching_stops_are_not_confirmed_cover() -> None:
    """A not-yet-open stop is unconfirmed even when its geometry matches."""
    deployment = _deployment(product_id="ADA-USD")
    position = _position(
        deployment,
        product_id="ADA-USD",
        side=PositionSide.LONG,
        quantity="10",
        entry="0.40",
        stop="0.36",
        target="0.48",
    )
    for status, reason in (
        (OrderStatus.PENDING, "pending_not_confirmed"),
        (OrderStatus.UNKNOWN, "unknown_not_confirmed"),
    ):
        order = _order(
            deployment,
            position,
            status=status,
            stop="0.36",
            target="0.48",
            price="0.48",
            venue_order_id=f"ada-{status.value}",
        )
        snapshot = DeploymentSnapshot(deployment=deployment, positions=(position,), orders=(order,))
        evidence = _evidence(snapshot, position)
        assert evidence.status is ProtectionStatus.UNKNOWN
        assert evidence.covered_quantity == Decimal(0)
        assert evidence.uncovered_quantity == Decimal("10")
        assert evidence.venue_resting is False
        assert evidence.stop_side_valid is True
        assert evidence.stop_geometry_valid is True
        assert evidence.verified_at is None
        assert evidence.observed_at == (None if status is OrderStatus.UNKNOWN else _NOW)
        assert reason in evidence.reasons
        assert book_protection_status(snapshot, product_id="ADA-USD", position=position) is (
            ProtectionStatus.UNKNOWN
        )


def test_attached_child_pending_does_not_bypass_geometry_confirmation() -> None:
    """attached_entry_covers can be true for a pending child; status still is not covered."""
    deployment = _deployment(product_id="ETH-USD")
    position = _position(deployment, product_id="ETH-USD", side=PositionSide.SHORT)
    entry = _order(
        deployment,
        position,
        kind=OrderKind.POST_ONLY_LIMIT,
        side=OrderSide.SELL,
        status=OrderStatus.FILLED,
        filled="0.5",
        stop="3200",
        target="2700",
        price="3000",
        venue_order_id="entry-1",
    )
    entry = replace(entry, attached_child_venue_order_id="child-pending")
    child = _order(
        deployment,
        position,
        status=OrderStatus.PENDING,
        stop="3200",
        target="2700",
        price="2700",
        venue_order_id="child-pending",
        parent_order_id=entry.id,
    )
    fill = Fill(
        id=uuid4(),
        deployment_id=deployment.id,
        order_id=entry.id,
        venue_fill_id="entry-fill",
        price=Decimal("3000"),
        quantity=Decimal("0.5"),
        fee=Decimal("0"),
        filled_at=_NOW,
    )
    snapshot = DeploymentSnapshot(
        deployment=deployment,
        positions=(position,),
        orders=(entry, child),
        fills=(fill,),
    )
    assert attached_entry_covers(snapshot, position) is True
    evidence = _evidence(snapshot, position)
    assert evidence.status is ProtectionStatus.UNKNOWN
    assert evidence.covered_quantity == Decimal(0)
    assert "pending_not_confirmed" in evidence.reasons


def test_partial_fill_of_attached_child_does_not_cover_the_original_quantity() -> None:
    """Remaining quantity, not the original child size, is the covered amount."""
    deployment = _deployment(product_id="ETH-USD")
    position = _position(
        deployment,
        product_id="ETH-USD",
        side=PositionSide.LONG,
        quantity="0.5",
        entry="3000",
        stop="2800",
        target="3400",
    )
    child = _order(
        deployment,
        position,
        quantity="0.5",
        filled="0.2",
        stop="2800",
        target="3400",
        price="3400",
        venue_order_id="child-partial",
    )
    snapshot = DeploymentSnapshot(deployment=deployment, positions=(position,), orders=(child,))
    evidence = _evidence(snapshot, position)
    assert evidence.status is ProtectionStatus.UNPROTECTED
    assert evidence.covered_quantity == Decimal("0.3")
    assert evidence.uncovered_quantity == Decimal("0.2")
    assert evidence.venue_resting is True
    assert "partial_stop_quantity" in evidence.reasons
    assert "venue_stop_resting" not in evidence.reasons


def test_pyramiding_old_bracket_does_not_cover_the_added_quantity() -> None:
    """An old full-size stop does not cover inventory added after it was rested."""
    deployment = _deployment(product_id="ADA-USD")
    position = _position(
        deployment,
        product_id="ADA-USD",
        side=PositionSide.LONG,
        quantity="1.0",
        entry="0.40",
        stop="0.36",
        target="0.48",
    )
    old = _order(
        deployment,
        position,
        quantity="0.4",
        stop="0.36",
        target="0.48",
        price="0.48",
        venue_order_id="old-bracket",
    )
    snapshot = DeploymentSnapshot(deployment=deployment, positions=(position,), orders=(old,))
    evidence = _evidence(snapshot, position)
    assert evidence.status is ProtectionStatus.UNPROTECTED
    assert evidence.required_quantity == Decimal("1.0")
    assert evidence.covered_quantity == Decimal("0.4")
    assert evidence.uncovered_quantity == Decimal("0.6")
    assert evidence.stop_geometry_valid is True


def test_two_matching_stops_sum_without_counting_the_same_child_twice() -> None:
    """Distinct stops add; a second row of the same venue child does not."""
    deployment = _deployment(product_id="ETH-USD")
    position = _position(
        deployment,
        product_id="ETH-USD",
        side=PositionSide.LONG,
        quantity="1.0",
        entry="3000",
        stop="2800",
        target="3400",
    )
    first = _order(
        deployment,
        position,
        quantity="0.4",
        stop="2800",
        target="3400",
        price="3400",
        venue_order_id="stop-a",
    )
    second = _order(
        deployment,
        position,
        quantity="0.6",
        stop="2800",
        target="3400",
        price="3400",
        venue_order_id="stop-b",
    )
    together = DeploymentSnapshot(
        deployment=deployment, positions=(position,), orders=(first, second)
    )
    covered = _evidence(together, position)
    assert covered.status is ProtectionStatus.COVERED
    assert covered.covered_quantity == Decimal("1.0")
    duplicate = _order(
        deployment,
        position,
        quantity="0.4",
        stop="2800",
        target="3400",
        price="3400",
        venue_order_id="stop-a",
    )
    doubled = DeploymentSnapshot(
        deployment=deployment, positions=(position,), orders=(first, duplicate)
    )
    once = _evidence(doubled, position)
    assert once.status is ProtectionStatus.UNPROTECTED
    assert once.covered_quantity == Decimal("0.4")
    assert once.uncovered_quantity == Decimal("0.6")
    assert "duplicate_order_ignored" in once.reasons


def test_stale_mismatched_bracket_is_not_cover() -> None:
    """An old bracket at the previous stop and target does not protect the current book."""
    deployment = _deployment(product_id="ETH-USD")
    position = _position(
        deployment,
        product_id="ETH-USD",
        side=PositionSide.LONG,
        quantity="0.5",
        entry="3000",
        stop="2800",
        target="3400",
    )
    stale = _order(
        deployment,
        position,
        stop="2700",
        target="3300",
        price="3300",
        venue_order_id="stale-1",
    )
    snapshot = DeploymentSnapshot(deployment=deployment, positions=(position,), orders=(stale,))
    evidence = _evidence(snapshot, position)
    assert evidence.status is ProtectionStatus.UNPROTECTED
    assert evidence.covered_quantity == Decimal(0)
    assert evidence.stop_geometry_valid is False
    assert "stale_bracket" in evidence.reasons
    assert "stop_price_mismatch" in evidence.reasons


def test_wrong_side_stop_is_not_cover() -> None:
    """A buy stop does not protect a long book."""
    deployment = _deployment(product_id="BTC-USD")
    position = _position(
        deployment,
        product_id="BTC-USD",
        side=PositionSide.LONG,
        quantity="0.01",
        entry="100",
        stop="90",
        target="120",
    )
    wrong = _order(
        deployment,
        position,
        side=OrderSide.BUY,
        kind=OrderKind.STOP_LIMIT,
        stop="90",
        venue_order_id="wrong-side",
    )
    snapshot = DeploymentSnapshot(deployment=deployment, positions=(position,), orders=(wrong,))
    evidence = _evidence(snapshot, position)
    assert evidence.status is ProtectionStatus.UNPROTECTED
    assert evidence.stop_side_valid is False
    assert evidence.stop_side is OrderSide.SELL
    assert "closing_side_mismatch" in evidence.reasons


def test_marketable_stop_without_geometry_is_not_resting_cover() -> None:
    """A working marketable exit is not a resting stop, even with a stop purpose."""
    deployment = _deployment(product_id="ETH-USD")
    position = _position(deployment, product_id="ETH-USD", side=PositionSide.SHORT)
    intent = OrderIntent(
        id=uuid4(),
        deployment_id=deployment.id,
        client_order_id="stop",
        purpose=IntentPurpose.STOP,
        side=OrderSide.BUY,
        kind=OrderKind.MARKETABLE,
        quantity=position.quantity,
        created_at=_NOW,
        candle_starts_at=_NOW,
        product_id="ETH-USD",
    )
    order = _order(
        deployment,
        position,
        kind=OrderKind.MARKETABLE,
        venue_order_id=None,
        intent_id=intent.id,
    )
    snapshot = DeploymentSnapshot(
        deployment=deployment, positions=(position,), orders=(order,), intents=(intent,)
    )
    evidence = _evidence(snapshot, position)
    assert evidence.status is ProtectionStatus.UNPROTECTED
    assert evidence.venue_resting is False
    assert "no_resting_stop" in evidence.reasons


def test_paper_synthetic_stop_is_covered_but_not_venue_resting() -> None:
    """Paper cover names the worker and does not invent a verification time."""
    deployment = _deployment(mode=DeploymentMode.PAPER, product_id="ETH-USD")
    position = _position(deployment, product_id="ETH-USD", side=PositionSide.SHORT)
    take_profit = _order(
        deployment,
        position,
        kind=OrderKind.POST_ONLY_LIMIT,
        price="2700",
        venue_order_id="paper-tp",
    )
    snapshot = DeploymentSnapshot(
        deployment=deployment, positions=(position,), orders=(take_profit,)
    )
    evidence = _evidence(snapshot, position)
    assert evidence.status is ProtectionStatus.COVERED
    assert evidence.mechanism.value == "synthetic"
    assert evidence.worker_dependent is True
    assert evidence.venue_resting is False
    assert evidence.observed_at is None
    assert evidence.verified_at is None
    assert evidence.covered_quantity == position.quantity
    assert "synthetic_worker_dependent" in evidence.reasons
    assert "venue_stop_resting" not in evidence.reasons


def test_position_and_sleeve_payloads_carry_the_same_evidence() -> None:
    """Deployment positions and portfolio books expose the strict evidence object."""
    deployment = _deployment(product_id="ADA-USD")
    position = _position(
        deployment,
        product_id="ADA-USD",
        side=PositionSide.LONG,
        quantity="20",
        entry="0.40",
        stop="0.36",
        target="0.48",
    )
    bracket = _order(
        deployment,
        position,
        stop="0.36",
        price="0.48",
        venue_order_id="ada-full",
    )
    snapshot = DeploymentSnapshot(
        deployment=deployment, position=position, positions=(position,), orders=(bracket,)
    )
    body = _position_response(position, snapshot, compatibility_focus=False)
    assert body.protection_status == "covered"
    assert body.protection.mechanism == "venue"
    assert body.protection.required_quantity == "20"
    assert body.protection.covered_quantity == "20"
    assert body.protection.uncovered_quantity == "0"
    assert body.protection.stop_side == "sell"
    assert body.protection.worker_dependent is False
    assert body.model_dump(mode="json")["protection"]["verified_at"] == _NOW.isoformat()
    assert body.protection.observation_source == "venue_order_state"
    assert body.protection.freshness == "recent_venue"
    assert body.protection.geometry_basis == "working_target"
    sleeve = open_books(snapshot, {})[0]
    assert sleeve.protection == body.protection
    assert sleeve.position_state == "open_protected"
