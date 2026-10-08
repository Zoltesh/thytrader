"""Build one ``BarDecision`` from a bar's before/after snapshots and loop observations.

Pure and deterministic: the worker passes the focused snapshot before the closed-bar
call, the snapshot it returned, what the loop reported (``DecisionObservations``), and
when the previous decision for this product was written. The builder classifies the
bar, links intents/orders/fills, explains the rule, and writes a one-line summary.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.decimal_text import canonical_decimal
from thytrader.execution.decision_classify import _classify, _skip
from thytrader.execution.decision_context import (
    _ACTIVE,
    BarContext,
    _order_for,
    _purpose,
    _text,
    _Window,
    _window,
)
from thytrader.execution.decision_rules import entry_rule_trace, exit_rule_trace
from thytrader.execution.decision_summary import _status_skip_summary, _summary
from thytrader.execution.decisions import (
    BarDecision,
    DecisionFill,
    DecisionOrder,
    DecisionOutcome,
    DecisionPosition,
    DecisionProtectionUpdate,
    DecisionSkipReason,
)
from thytrader.market_data.models import as_dataset_timeframe, parse_candle_interval
from thytrader.trading.models import (
    IntentPurpose,
    OrderKind,
    OrderStatus,
    resolved_product_id,
    snapshot_positions,
)

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.execution.decisions import EntryRuleTrace, ExitRuleTrace
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.trading.models import DeploymentSnapshot, Fill, Order, Position

_PROTECTION_PURPOSES = frozenset(
    {IntentPurpose.TAKE_PROFIT, IntentPurpose.STOP, IntentPurpose.BRACKET}
)
_SUMMARY_LIMIT = 500
_MAX_LINKED = 50


def build_bar_decision(context: BarContext) -> BarDecision:
    """Classify one processed bar and assemble its validated decision record."""
    after = context.after or context.before
    deployment = after.deployment
    window = _window(context)
    rule = _rule(context)
    exit_rule = _exit_rule(context)
    classified = _classify(context, window, rule)
    interval = parse_candle_interval(deployment.timeframe or context.strategy.timeframe)
    linked = window.orders[:_MAX_LINKED]
    return BarDecision(
        deployment_id=deployment.id,
        strategy_id=deployment.strategy_id,
        strategy_fingerprint=deployment.strategy_fingerprint,
        product_id=context.product_id,
        timeframe=as_dataset_timeframe(interval),
        mode=deployment.mode,
        bar_starts_at=context.bar_starts_at,
        bar_closes_at=context.bar_starts_at + interval.duration,
        evaluated_at=context.evaluated_at,
        outcome=classified.outcome,
        reason_code=classified.reason_code,
        summary=_bar_summary(_summary(context, window, rule, classified, exit_rule), context),
        skip_reason=classified.skip_reason,
        exit_reason=classified.exit_reason,
        action=classified.action,
        intent_id=classified.intent_id,
        order_ids=tuple(order.id for order in linked),
        orders=tuple(_order(order, window) for order in linked),
        fills=tuple(_fill(fill, window) for fill in window.fills[:_MAX_LINKED]),
        close_price=_text(context.close_price),
        rule=rule,
        exit_rule=exit_rule,
        risk=classified.risk,
        position=_position(after.position),
        no_trade_bar=context.no_trade_bar,
        protection_update=_protection_update(context, window),
    )


_NO_TRADE_SUFFIX = " (no-trade bar: no trades, flat at the prior close)"


def _bar_summary(summary: str, context: BarContext) -> str:
    """Bound the summary and name a no-trade bar so a flat close is never misread."""
    if not context.no_trade_bar:
        return summary[:_SUMMARY_LIMIT]
    return summary[: _SUMMARY_LIMIT - len(_NO_TRADE_SUFFIX)] + _NO_TRADE_SUFFIX


def _protection_update(context: BarContext, window: _Window) -> DecisionProtectionUpdate | None:
    """Link observed protective cancellations to current coverage without inferring entries."""
    canceled = tuple(
        order.id
        for order in window.orders
        if order.status is OrderStatus.CANCELED
        and order.kind is not OrderKind.MARKETABLE
        and _purpose(order, window) in _PROTECTION_PURPOSES
    )
    if not canceled:
        return None
    after = context.after or context.before
    position = after.position
    active = tuple(
        order
        for order in after.orders
        if order.status in _ACTIVE
        and order.kind is not OrderKind.MARKETABLE
        and _purpose(order, window) in _PROTECTION_PURPOSES
        and resolved_product_id(order.product_id, after.deployment) == context.product_id
    )
    coverage = sum(
        (
            order.quantity - order.filled_quantity
            for order in active
            if order.status is OrderStatus.OPEN
        ),
        Decimal(0),
    )
    quantity = Decimal(0) if position is None else position.quantity
    previous = context.before.position
    return DecisionProtectionUpdate(
        kind="replacement" if active else "canceled_without_replacement",
        canceled_order_ids=canceled[:_MAX_LINKED],
        active_order_ids=tuple(order.id for order in active[:_MAX_LINKED]),
        previous_stop_price=None if previous is None else _text(previous.stop_price),
        stop_price=None if position is None else _text(position.stop_price),
        target_price=None if position is None else _text(position.target_price),
        coverage_quantity=canonical_decimal(coverage),
        position_quantity=canonical_decimal(quantity),
        fully_covered=quantity > 0 and coverage >= quantity,
    )


def build_gate_skip_decision(
    *,
    snapshot: DeploymentSnapshot,
    strategy: StrategyDefinition,
    product_id: str,
    bar_starts_at: datetime,
    evaluated_at: datetime,
    reason: DecisionSkipReason,
    detail: str,
) -> BarDecision:
    """Record a bar the worker could not evaluate (data gap or user-feed gate)."""
    deployment = snapshot.deployment
    interval = parse_candle_interval(deployment.timeframe or strategy.timeframe)
    classified = _skip(reason, detail=detail)
    position = next(
        (
            item
            for item in snapshot_positions(snapshot)
            if resolved_product_id(item.product_id, deployment) == product_id
        ),
        None,
    )
    return BarDecision(
        deployment_id=deployment.id,
        strategy_id=deployment.strategy_id,
        strategy_fingerprint=deployment.strategy_fingerprint,
        product_id=product_id,
        timeframe=as_dataset_timeframe(interval),
        mode=deployment.mode,
        bar_starts_at=bar_starts_at,
        bar_closes_at=bar_starts_at + interval.duration,
        evaluated_at=evaluated_at,
        outcome=DecisionOutcome.SKIPPED,
        reason_code=classified.reason_code,
        summary=_status_skip_summary(deployment, classified)[:_SUMMARY_LIMIT],
        skip_reason=reason,
        position=_position(position),
    )


def _rule(context: BarContext) -> EntryRuleTrace | None:
    """Explain the evaluated entry rule when the loop evaluated it on this bar."""
    observations = context.observations
    if observations is None or observations.evaluation is None:
        return None
    return entry_rule_trace(context.strategy, observations.evaluation)


def _exit_rule(context: BarContext) -> ExitRuleTrace | None:
    """Explain the evaluated ``exits.signal_exit`` rule when the loop evaluated it."""
    observations = context.observations
    if observations is None or observations.exit_evaluation is None:
        return None
    return exit_rule_trace(context.strategy, observations.exit_evaluation)


def _order(order: Order, window: _Window) -> DecisionOrder:
    """Project one linked order."""
    return DecisionOrder(
        order_id=order.id,
        intent_id=order.intent_id,
        purpose=_purpose(order, window),
        side=order.side,
        kind=order.kind,
        status=order.status,
        quantity=canonical_decimal(order.quantity),
        price=_text(order.price),
        filled_quantity=canonical_decimal(order.filled_quantity),
        created_at=order.created_at,
        stop_trigger_price=_text(order.stop_trigger_price),
        take_profit_price=_text(order.take_profit_price),
        parent_order_id=order.parent_order_id,
    )


def _fill(fill: Fill, window: _Window) -> DecisionFill:
    """Project one window fill with its order's purpose and side."""
    order = _order_for(fill, window)
    return DecisionFill(
        fill_id=fill.id,
        order_id=fill.order_id,
        purpose=None if order is None else _purpose(order, window),
        side=None if order is None else order.side,
        price=canonical_decimal(fill.price),
        quantity=canonical_decimal(fill.quantity),
        fee=canonical_decimal(fill.fee),
        filled_at=fill.filled_at,
    )


def _position(position: Position | None) -> DecisionPosition | None:
    """Project the end-of-bar book."""
    if position is None:
        return None
    return DecisionPosition(
        side=position.side,
        quantity=canonical_decimal(position.quantity),
        entry_price=canonical_decimal(position.entry_price),
        stop_price=canonical_decimal(position.stop_price),
        target_price=_text(position.target_price),
    )
