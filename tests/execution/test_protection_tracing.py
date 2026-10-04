"""Protection replacements explain order churn without inventing failed entries."""

from dataclasses import replace

from tests.execution.test_decision_builder import _context, _deployment, _intent, _order, _position
from thytrader.execution.decision_builder import build_bar_decision
from thytrader.execution.decisions import DecisionOutcome
from thytrader.execution.models import (
    DeploymentSnapshot,
    IntentPurpose,
    OrderKind,
    OrderStatus,
    RuntimePhase,
)


def test_replacement_links_old_and_new_orders_and_confirmed_quantity() -> None:
    """A ratchet retains holding outcome and reports exact old/new protective identities."""
    old, new = _intent(IntentPurpose.BRACKET), _intent(IntentPurpose.BRACKET)
    old_order, new_order = _order(old, OrderStatus.OPEN), _order(new, OrderStatus.OPEN)
    position = _position()
    before = DeploymentSnapshot(
        deployment=_deployment(phase=RuntimePhase.PENDING_EXIT),
        position=position,
        intents=(old,),
        orders=(old_order,),
    )
    after = replace(
        before,
        intents=(old, new),
        orders=(replace(old_order, status=OrderStatus.CANCELED), new_order),
        position=replace(position, stop_price=position.stop_price + 1),
    )
    decision = build_bar_decision(_context(before, after))
    assert decision.outcome is DecisionOutcome.HOLDING
    update = decision.protection_update
    assert update is not None
    assert update.canceled_order_ids == (old_order.id,)
    assert update.active_order_ids == (new_order.id,)
    assert update.previous_stop_price != update.stop_price
    uncertain = build_bar_decision(
        _context(
            before,
            replace(
                after,
                orders=(
                    replace(old_order, status=OrderStatus.CANCELED),
                    replace(new_order, status=OrderStatus.UNKNOWN),
                ),
            ),
        )
    )
    assert uncertain.protection_update is not None
    assert uncertain.protection_update.fully_covered is False
    assert uncertain.protection_update.coverage_quantity == "0"


def test_canceled_marketable_exit_does_not_claim_protection_replacement() -> None:
    """A canceled time exit is not a resting protective bracket or stop."""
    intent = _intent(IntentPurpose.TIME_EXIT)
    order = replace(_order(intent, OrderStatus.OPEN), kind=OrderKind.MARKETABLE)
    before = DeploymentSnapshot(
        deployment=_deployment(phase=RuntimePhase.PENDING_EXIT),
        position=_position(),
        intents=(intent,),
        orders=(order,),
    )
    after = replace(before, orders=(replace(order, status=OrderStatus.CANCELED),))
    assert build_bar_decision(_context(before, after)).protection_update is None
