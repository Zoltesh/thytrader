"""Bar context and decision window shared by the bar-decision builder stages.

``BarContext`` carries everything known about one processed bar; ``_window`` collects
the intents, orders, and fills that belong to that bar's decision exactly once, and
``_Classified`` is the outcome the classifier hands to the summary writer and builder.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from thytrader.decimal_text import canonical_decimal
from thytrader.execution.decisions import (
    DecisionAction,
    DecisionExitReason,
    DecisionOutcome,
    DecisionRisk,
    DecisionSkipReason,
)
from thytrader.trading.models import IntentPurpose, OrderStatus

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal
    from uuid import UUID

    from thytrader.execution.decision_scope import DecisionObservations
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.trading.models import DeploymentSnapshot, Fill, Order, OrderIntent


_ACTIVE = frozenset({OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.UNKNOWN})


@dataclass(frozen=True, slots=True)
class BarContext:
    """Everything known about one processed bar for one covered product.

    ``before`` is the focused snapshot passed into the closed-bar call and ``after``
    the snapshot it returned (None when the call raised). ``previous_evaluated_at`` is
    when the previous bar's decision was written, so fills applied between bars (for
    example a venue bracket) are attributed to this bar's window exactly once.
    ``no_trade_bar`` marks a flat zero-volume bar for an interval without trades
    (ADR 0095); it is evaluated like any bar and the record names it.
    """

    strategy: StrategyDefinition
    product_id: str
    bar_starts_at: datetime
    close_price: Decimal | None
    evaluated_at: datetime
    allow_new_entries: bool
    before: DeploymentSnapshot
    after: DeploymentSnapshot | None
    observations: DecisionObservations | None
    previous_evaluated_at: datetime | None = None
    error: str | None = None
    no_trade_bar: bool = False


@dataclass(frozen=True, slots=True)
class _Window:
    """Intents, orders, and fills that belong to this bar's decision window."""

    new_intents: tuple[OrderIntent, ...]
    orders: tuple[Order, ...]
    fills: tuple[Fill, ...]
    intents: dict[UUID, OrderIntent]
    before_orders: dict[UUID, Order]


@dataclass(frozen=True, slots=True)
class _Classified:
    """Outcome, reason, and linked identity before summary text is attached."""

    outcome: DecisionOutcome
    reason_code: str
    skip_reason: DecisionSkipReason | None = None
    exit_reason: DecisionExitReason | None = None
    action: DecisionAction = DecisionAction.NONE
    intent_id: UUID | None = None
    risk: DecisionRisk | None = None
    detail: str = ""


def _window(context: BarContext) -> _Window:
    """Collect new intents, changed orders, and fills applied since the last decision."""
    before = context.before
    after = context.after or before
    known_intents = {intent.id for intent in before.intents}
    before_orders = {order.id: order for order in before.orders}
    known_fills = {fill.id for fill in before.fills}
    fills = tuple(
        sorted(
            (
                fill
                for fill in after.fills
                if fill.id not in known_fills or _applied_since(fill, context.previous_evaluated_at)
            ),
            key=lambda fill: (fill.filled_at, str(fill.id)),
        )
    )
    filled_orders = {fill.order_id for fill in fills}
    orders = tuple(
        order
        for order in sorted(after.orders, key=lambda order: (order.created_at, str(order.id)))
        if order.id not in before_orders
        or before_orders[order.id].status is not order.status
        or order.id in filled_orders
    )
    return _Window(
        new_intents=tuple(intent for intent in after.intents if intent.id not in known_intents),
        orders=orders,
        fills=fills,
        intents={intent.id: intent for intent in (*before.intents, *after.intents)},
        before_orders=before_orders,
    )


def _applied_since(fill: Fill, since: datetime | None) -> bool:
    """Whether a fill's economics landed after the previous decision was written."""
    if since is None:
        return False
    stamp = fill.economics_applied_at or fill.filled_at
    return stamp > since


def _purpose(order: Order, window: _Window) -> IntentPurpose | None:
    """Effective purpose: venue-attached children of an entry act as brackets."""
    if order.parent_order_id is not None:
        return IntentPurpose.BRACKET
    intent = window.intents.get(order.intent_id)
    return None if intent is None else intent.purpose


def _order_for(fill: Fill, window: _Window) -> Order | None:
    """The linked order for one window fill."""
    return next((order for order in window.orders if order.id == fill.order_id), None)


def _text(value: Decimal | None) -> str | None:
    """Canonical Decimal text, or None."""
    return None if value is None else canonical_decimal(value)
