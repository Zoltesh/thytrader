"""Classify one processed bar into its decision outcome and reason.

Applies the fixed outcome precedence (error, entry, exit, pending signal exit, entry
skip, block, evaluated rule, cancel, hold, skip), names exit reasons from the linked
intent or filled order, and projects risk verdicts onto the journal record.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from thytrader.evaluation.trace import EntryConditionOutcome
from thytrader.execution.decision_context import (
    _ACTIVE,
    BarContext,
    _Classified,
    _order_for,
    _purpose,
    _Window,
)
from thytrader.execution.decisions import (
    DecisionAction,
    DecisionExitReason,
    DecisionOutcome,
    DecisionRisk,
    DecisionSkipReason,
)
from thytrader.risk.models import RiskDecision
from thytrader.strategies.models import atr_trailing_stop
from thytrader.trading.geometry import entry_skip_category, entry_skip_detail
from thytrader.trading.models import (
    DeploymentStatus,
    IntentPurpose,
    LifecycleCommand,
    OrderKind,
    OrderStatus,
    RuntimePhase,
)

if TYPE_CHECKING:
    from decimal import Decimal
    from uuid import UUID

    from thytrader.execution.decisions import EntryRuleTrace
    from thytrader.risk.models import RiskVerdict
    from thytrader.trading.models import Order, OrderIntent


_EXIT_PURPOSES = frozenset(
    {
        IntentPurpose.TAKE_PROFIT,
        IntentPurpose.STOP,
        IntentPurpose.TIME_EXIT,
        IntentPurpose.BRACKET,
        IntentPurpose.SIGNAL_EXIT,
    }
)


_MARKET_EXITS = frozenset({IntentPurpose.STOP, IntentPurpose.TIME_EXIT, IntentPurpose.SIGNAL_EXIT})


_SUBMITTED = frozenset(
    {OrderStatus.OPEN, OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELED}
)


# Mirrors the execution worker's USER_FEED_PAUSE_DETAIL (pinned by a unit test).
USER_FEED_GATE_DETAIL = "User-order feed is not connected."


_GAP_MARKER = "window is gapped"


def _classify(context: BarContext, window: _Window, rule: EntryRuleTrace | None) -> _Classified:
    """Classify the bar, then attach a tripped breaker's verdict when no other risk applies."""
    classified = _classify_outcome(context, window, rule)
    breaker = context.observations.breaker if context.observations is not None else None
    if classified.risk is None and breaker is not None:
        return replace(classified, risk=_risk(breaker))
    return classified


def _classify_outcome(
    context: BarContext, window: _Window, rule: EntryRuleTrace | None
) -> _Classified:
    """Apply the outcome precedence: error, entry, exit, block, rule, cancel, hold, skip."""
    for step in (_error, _entry, _exit, _signal_exit_pending, _entry_skipped, _blocked):
        found = step(context, window)
        if found is not None:
            return found
    evaluated = _evaluated(context, rule)
    if evaluated is not None:
        return evaluated
    canceled = _canceled(context, window)
    if canceled is not None:
        return canceled
    after = context.after or context.before
    if after.position is not None:
        return _Classified(outcome=DecisionOutcome.HOLDING, reason_code="HOLDING")
    return _skipped(context)


def _error(context: BarContext, window: _Window) -> _Classified | None:
    """A raised cycle or a fail-closed signal evaluation is an error bar."""
    del window
    observations = context.observations
    if context.error is not None:
        return _Classified(
            outcome=DecisionOutcome.ERROR, reason_code="CYCLE_ERROR", detail=context.error
        )
    if observations is not None and observations.evaluation_error is not None:
        return _Classified(
            outcome=DecisionOutcome.ERROR,
            reason_code="EVALUATION_ERROR",
            detail=observations.evaluation_error,
        )
    return None


def _entry(context: BarContext, window: _Window) -> _Classified | None:
    """A new entry intent: a signal entry, or a reprice of a working entry."""
    intent = next(
        (item for item in window.new_intents if item.purpose is IntentPurpose.ENTRY), None
    )
    if intent is None:
        return None
    risk = _risk(_last_verdict(context, RiskDecision.ALLOW))
    observations = context.observations
    if observations is None or observations.evaluation is None:
        return _Classified(
            outcome=DecisionOutcome.SKIPPED,
            reason_code="PENDING_ENTRY",
            skip_reason=DecisionSkipReason.PENDING_ENTRY,
            action=DecisionAction.REPRICED,
            intent_id=intent.id,
            risk=risk,
        )
    return _Classified(
        outcome=DecisionOutcome.ENTRY_SIGNAL,
        reason_code="SIGNAL_MATCHED",
        action=_submit_action(intent, window),
        intent_id=intent.id,
        risk=risk,
    )


def _submit_action(intent: OrderIntent, window: _Window) -> DecisionAction:
    """Order reached the venue (or paper book) versus only a persisted intent."""
    order = next((item for item in window.orders if item.intent_id == intent.id), None)
    if order is not None and order.status in _SUBMITTED:
        return DecisionAction.ORDER_SUBMITTED
    return DecisionAction.INTENT_CREATED


def _exit(context: BarContext, window: _Window) -> _Classified | None:
    """A new marketable exit intent, or an exit order whose fill landed in the window."""
    intent = next((item for item in window.new_intents if item.purpose in _MARKET_EXITS), None)
    if intent is not None:
        return _exit_classified(
            _market_exit_reason(context, intent.purpose),
            action=_submit_action(intent, window),
            intent_id=intent.id,
        )
    for fill in window.fills:
        order = _order_for(fill, window)
        if order is not None and _purpose(order, window) in _EXIT_PURPOSES:
            return _exit_classified(
                _filled_exit_reason(context, order, window, fill.price),
                action=DecisionAction.NONE,
                intent_id=order.intent_id,
            )
    return None


def _signal_exit_pending(context: BarContext, window: _Window) -> _Classified | None:
    """A matched exit rule whose marketable exit waits on a protection cancel (ADR 0093).

    The book still holds the position at the end of the bar; the worker keeps exiting on
    later cycles, so the bar is an exit in progress, not a hold.
    """
    observations = context.observations
    after = context.after or context.before
    if observations is None or observations.exit_evaluation is None or after.position is None:
        return None
    if observations.exit_evaluation.outcome is not EntryConditionOutcome.MATCHED:
        return None
    canceled = any(
        order.status is OrderStatus.CANCELED
        and order.id in window.before_orders
        and window.before_orders[order.id].status in _ACTIVE
        for order in window.orders
    )
    return _Classified(
        outcome=DecisionOutcome.EXIT,
        reason_code="EXIT_SIGNAL",
        exit_reason=DecisionExitReason.SIGNAL,
        action=DecisionAction.ORDER_CANCELED if canceled else DecisionAction.NONE,
        detail="the exit rule matched; protection is being canceled before the sell",
    )


def _exit_classified(
    reason: DecisionExitReason, *, action: DecisionAction, intent_id: UUID
) -> _Classified:
    """One exit bar with an ``EXIT_<REASON>`` code."""
    return _Classified(
        outcome=DecisionOutcome.EXIT,
        reason_code=f"EXIT_{reason.value.upper()}",
        exit_reason=reason,
        action=action,
        intent_id=intent_id,
    )


def _market_exit_reason(context: BarContext, purpose: IntentPurpose) -> DecisionExitReason:
    """Time and signal exits, flatten commands, and stop/trail exits sent marketably."""
    if purpose is IntentPurpose.TIME_EXIT:
        return DecisionExitReason.TIME
    if purpose is IntentPurpose.SIGNAL_EXIT:
        return DecisionExitReason.SIGNAL
    deployment = (context.after or context.before).deployment
    if deployment.lifecycle_command is LifecycleCommand.FLATTEN:
        return DecisionExitReason.FLATTEN
    return _stop_or_trail(context)


def _filled_exit_reason(
    context: BarContext, order: Order, window: _Window, price: Decimal
) -> DecisionExitReason:
    """Name a filled resting exit: take-profit, time, or the stop/target leg of a bracket."""
    purpose = _purpose(order, window)
    if purpose is IntentPurpose.TAKE_PROFIT:
        return DecisionExitReason.TARGET
    if purpose in _MARKET_EXITS and purpose is not None:
        return _market_exit_reason(context, purpose)
    if order.kind is OrderKind.STOP_LIMIT:
        return _stop_or_trail(context)
    stop = order.stop_trigger_price
    target = order.take_profit_price or order.price
    if stop is not None and target is not None and abs(price - target) < abs(price - stop):
        return DecisionExitReason.TARGET
    return _stop_or_trail(context)


def _stop_or_trail(context: BarContext) -> DecisionExitReason:
    """A stop exit is a trailing exit once an ATR trail has ratcheted the stop."""
    position = context.before.position
    trailing = atr_trailing_stop(context.strategy.exits) is not None
    if trailing and position is not None and position.trail_extreme is not None:
        return DecisionExitReason.TRAIL
    return DecisionExitReason.STOP


def _entry_skipped(context: BarContext, window: _Window) -> _Classified | None:
    """A matched signal whose stop/target geometry or sizing rested no entry (ADR 0090)."""
    del window
    observations = context.observations
    if observations is None or observations.entry_skip is None:
        return None
    reason = observations.entry_skip
    category = (
        DecisionSkipReason.ENTRY_GEOMETRY
        if entry_skip_category(reason) == "geometry"
        else DecisionSkipReason.ENTRY_SIZING
    )
    return _Classified(
        outcome=DecisionOutcome.SKIPPED,
        reason_code=reason.value.upper(),
        skip_reason=category,
        detail=entry_skip_detail(reason),
    )


def _blocked(context: BarContext, window: _Window) -> _Classified | None:
    """A matched signal refused by risk, freshness, sizing, or venue prerequisites."""
    del window
    observations = context.observations
    if observations is None:
        return None
    denied = _last_verdict(context, RiskDecision.DENY) or observations.freshness
    if denied is not None and denied.decision is RiskDecision.DENY:
        return _Classified(
            outcome=DecisionOutcome.ENTRY_BLOCKED,
            reason_code=denied.reason_code.value,
            risk=_risk(denied),
            detail=denied.detail,
        )
    code = observations.entry_block_code
    if code is None or code == "PYRAMID_DISABLED":
        return None
    return _Classified(
        outcome=DecisionOutcome.ENTRY_BLOCKED,
        reason_code=code,
        detail=observations.entry_block_detail or code,
    )


def _evaluated(context: BarContext, rule: EntryRuleTrace | None) -> _Classified | None:
    """Classify an evaluated rule that produced no order and no block."""
    observations = context.observations
    if observations is None or observations.evaluation is None:
        return None
    holding = (context.after or context.before).position is not None
    outcome = observations.evaluation.outcome
    if holding:
        return _Classified(outcome=DecisionOutcome.HOLDING, reason_code="HOLDING")
    if outcome is EntryConditionOutcome.UNDEFINED:
        return _skip(DecisionSkipReason.WARMUP)
    if outcome is EntryConditionOutcome.MATCHED:
        return _Classified(
            outcome=DecisionOutcome.ENTRY_BLOCKED,
            reason_code="ENTRY_NOT_SUBMITTED",
            detail="The rule matched but no entry order was created on this bar.",
        )
    htf_only = (
        rule is not None
        and rule.htf_filter is not None
        and observations.evaluation.ltf_outcome is EntryConditionOutcome.MATCHED
    )
    return _Classified(
        outcome=DecisionOutcome.NO_SIGNAL,
        reason_code="HTF_FILTER_NOT_MET" if htf_only else "CONDITIONS_NOT_MET",
    )


def _canceled(context: BarContext, window: _Window) -> _Classified | None:
    """A known entry canceled on this bar, excluding protective order maintenance."""
    canceled = next(
        (
            order
            for order in window.orders
            if order.status is OrderStatus.CANCELED
            and _purpose(order, window) is IntentPurpose.ENTRY
            and order.id in window.before_orders
            and window.before_orders[order.id].status in _ACTIVE
        ),
        None,
    )
    if canceled is None:
        return None
    stopped = (context.after or context.before).deployment.status is DeploymentStatus.STOPPED
    reason = DecisionSkipReason.STOPPED if stopped else DecisionSkipReason.PENDING_ENTRY
    return _Classified(
        outcome=DecisionOutcome.SKIPPED,
        reason_code=reason.value.upper(),
        skip_reason=reason,
        action=DecisionAction.ORDER_CANCELED,
        intent_id=canceled.intent_id,
    )


def _skipped(context: BarContext) -> _Classified:
    """Name why a flat bar was not evaluated."""
    after = context.after or context.before
    deployment = after.deployment
    observations = context.observations
    if observations is not None and observations.breaker is not None:
        return _Classified(
            outcome=DecisionOutcome.SKIPPED,
            reason_code=DecisionSkipReason.PAUSED.value.upper(),
            skip_reason=DecisionSkipReason.PAUSED,
            risk=_risk(observations.breaker),
            detail=observations.breaker.detail,
        )
    if observations is not None and observations.entry_gate is not None:
        return _skip(observations.entry_gate)
    if deployment.status is DeploymentStatus.STOPPED:
        return _skip(DecisionSkipReason.STOPPED)
    if deployment.status is DeploymentStatus.PAUSED:
        detail = deployment.mismatch_detail or ""
        return _skip(pause_skip_reason(detail), detail=detail)
    if deployment.phase is RuntimePhase.PENDING_ENTRY:
        return _skip(DecisionSkipReason.PENDING_ENTRY)
    if deployment.lifecycle_command is LifecycleCommand.STOP_NEW_ENTRIES:
        return _skip(DecisionSkipReason.ENTRIES_DISABLED)
    if observations is not None and observations.reference_gate is not None:
        gate = observations.reference_gate
        return _skip(gate.reason, detail=gate.detail)
    if not context.allow_new_entries:
        return _skip(DecisionSkipReason.CATCH_UP)
    return _Classified(outcome=DecisionOutcome.SKIPPED, reason_code="NOT_EVALUATED")


def pause_skip_reason(detail: str) -> DecisionSkipReason:
    """Map a worker pause detail onto the gate that caused it (feed, data gap, other)."""
    if detail == USER_FEED_GATE_DETAIL:
        return DecisionSkipReason.USER_FEED_GATE
    if _GAP_MARKER in detail:
        return DecisionSkipReason.DATA_GAP
    return DecisionSkipReason.PAUSED


def _skip(reason: DecisionSkipReason, *, detail: str = "") -> _Classified:
    """One skipped bar with a stable upper-case reason code."""
    return _Classified(
        outcome=DecisionOutcome.SKIPPED,
        reason_code=reason.value.upper(),
        skip_reason=reason,
        detail=detail,
    )


def _last_verdict(context: BarContext, decision: RiskDecision) -> RiskVerdict | None:
    """The newest risk-gate verdict with one decision reported on this bar."""
    observations = context.observations
    if observations is None:
        return None
    return next(
        (item for item in reversed(observations.risk_verdicts) if item.decision is decision),
        None,
    )


def _risk(verdict: RiskVerdict | None) -> DecisionRisk | None:
    """Project one risk verdict onto the journal record."""
    if verdict is None:
        return None
    return DecisionRisk(
        decision="allow" if verdict.decision is RiskDecision.ALLOW else "deny",
        reason_code=verdict.reason_code.value,
        detail=verdict.detail,
    )
