"""Build one ``BarDecision`` from a bar's before/after snapshots and loop observations.

Pure and deterministic: the worker passes the focused snapshot before the closed-bar
call, the snapshot it returned, what the loop reported (``DecisionObservations``), and
when the previous decision for this product was written. The builder classifies the
bar, links intents/orders/fills, explains the rule, and writes a one-line summary.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.decision_rules import (
    count_unmet_leaves,
    display_decimal,
    entry_rule_trace,
    exit_rule_trace,
    first_unmet,
    met_text,
    unmet_text,
)
from thytrader.execution.decisions import (
    BarDecision,
    ConditionResult,
    DecisionAction,
    DecisionExitReason,
    DecisionFill,
    DecisionOrder,
    DecisionOutcome,
    DecisionPosition,
    DecisionRisk,
    DecisionSkipReason,
)
from thytrader.execution.geometry import entry_skip_category, entry_skip_detail
from thytrader.execution.models import (
    DeploymentStatus,
    IntentPurpose,
    LifecycleCommand,
    OrderKind,
    OrderStatus,
    RuntimePhase,
    resolved_product_id,
    snapshot_positions,
)
from thytrader.market_data.models import as_dataset_timeframe, parse_candle_interval
from thytrader.research.indicators import canonical_decimal
from thytrader.research.trace import EntryConditionOutcome
from thytrader.risk.models import RiskDecision
from thytrader.strategies.models import atr_trailing_stop

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from thytrader.execution.decision_scope import DecisionObservations
    from thytrader.execution.decisions import EntryRuleTrace, ExitRuleTrace
    from thytrader.execution.models import (
        Deployment,
        DeploymentSnapshot,
        Fill,
        Order,
        OrderIntent,
        Position,
    )
    from thytrader.risk.models import RiskVerdict
    from thytrader.strategies.models import StrategyDefinition

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
_ACTIVE = frozenset({OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.UNKNOWN})
_SUMMARY_LIMIT = 500
_MAX_LINKED = 50
# Mirrors the execution worker's USER_FEED_PAUSE_DETAIL (pinned by a unit test).
USER_FEED_GATE_DETAIL = "User-order feed is not connected."
_GAP_MARKER = "window is gapped"


@dataclass(frozen=True, slots=True)
class BarContext:
    """Everything known about one processed bar for one covered product.

    ``before`` is the focused snapshot passed into the closed-bar call and ``after``
    the snapshot it returned (None when the call raised). ``previous_evaluated_at`` is
    when the previous bar's decision was written, so fills applied between bars (for
    example a venue bracket) are attributed to this bar's window exactly once.
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
        summary=_summary(context, window, rule, classified, exit_rule)[:_SUMMARY_LIMIT],
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


def _purpose(order: Order, window: _Window) -> IntentPurpose | None:
    """Effective purpose: venue-attached children of an entry act as brackets."""
    if order.parent_order_id is not None:
        return IntentPurpose.BRACKET
    intent = window.intents.get(order.intent_id)
    return None if intent is None else intent.purpose


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


def _order_for(fill: Fill, window: _Window) -> Order | None:
    """The linked order for one window fill."""
    return next((order for order in window.orders if order.id == fill.order_id), None)


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
    """A working order canceled on this bar (abandoned entry or managed shutdown)."""
    canceled = next(
        (
            order
            for order in window.orders
            if order.status is OrderStatus.CANCELED
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


def _summary(
    context: BarContext,
    window: _Window,
    rule: EntryRuleTrace | None,
    classified: _Classified,
    exit_rule: ExitRuleTrace | None = None,
) -> str:
    """One human line per outcome, e.g. ``No trade: RSI(14) 47.21 needs ≥ 50``."""
    outcome = classified.outcome
    if outcome is DecisionOutcome.ENTRY_SIGNAL:
        return _entry_summary(window, rule, classified)
    if outcome is DecisionOutcome.NO_SIGNAL:
        return _no_signal_summary(rule)
    if outcome is DecisionOutcome.EXIT:
        return _exit_summary(window, classified, exit_rule)
    if outcome is DecisionOutcome.HOLDING:
        return _holding_summary(context, window, rule, exit_rule)
    if outcome is DecisionOutcome.ENTRY_BLOCKED:
        return f"Blocked: {classified.reason_code} — {classified.detail}"
    if outcome is DecisionOutcome.ERROR:
        return f"Error: {classified.detail or classified.reason_code}"
    return _skip_summary(context, window, rule, classified)


def _entry_summary(window: _Window, rule: EntryRuleTrace | None, classified: _Classified) -> str:
    """``Entry: RSI(14) 55.2 ≥ 50 → buy 0.01 @ 64000 (open)``."""
    reason = met_text(rule.entry) if rule is not None else "entry rule matched"
    order = next((item for item in window.orders if item.intent_id == classified.intent_id), None)
    if order is None:
        return f"Entry: {reason} → intent persisted; order not yet confirmed"
    price = f" @ {display_decimal(_text(order.price))}" if order.price is not None else ""
    quantity = display_decimal(_text(order.quantity))
    return f"Entry: {reason} → {order.side.value} {quantity}{price} ({order.status.value})"


def _no_signal_summary(rule: EntryRuleTrace | None) -> str:
    """Explain the first unmet leaf of the entry rule (or the HTF filter)."""
    if rule is None:
        return "No trade: entry conditions did not match"
    if rule.htf_filter is not None and rule.entry.result is ConditionResult.TRUE:
        node = first_unmet(rule.htf_filter.condition)
        if node is not None:
            return f"No trade: HTF {rule.htf_filter.timeframe} filter — {unmet_text(node)}"
    node = first_unmet(rule.entry)
    if node is None:
        return "No trade: entry conditions did not match"
    more = count_unmet_leaves(rule.entry) - 1
    suffix = f" (+{more} more unmet)" if more > 0 else ""
    return f"No trade: {unmet_text(node)}{suffix}"


def _exit_summary(
    window: _Window, classified: _Classified, exit_rule: ExitRuleTrace | None = None
) -> str:
    """``Exit (stop): sell 0.01 @ 63000`` from the exit fill, else the exit order.

    A signal exit names the rule that matched, e.g. ``Exit (signal): EMA(20) crosses below
    EMA(50) → sell 0.01 @ 61000``.
    """
    reason = classified.exit_reason.value if classified.exit_reason is not None else "exit"
    if exit_rule is not None and classified.exit_reason is DecisionExitReason.SIGNAL:
        matched = met_text(exit_rule.condition, fallback="exit rule matched")
        return _signal_exit_summary(window, classified, matched)
    fills = [
        fill
        for fill in window.fills
        if (order := _order_for(fill, window)) is not None
        and order.intent_id == classified.intent_id
    ]
    if fills:
        last = fills[-1]
        quantity = sum((fill.quantity for fill in fills), start=Decimal(0))
        order = _order_for(last, window)
        side = order.side.value if order is not None else "exit"
        return (
            f"Exit ({reason}): {side} {display_decimal(_text(quantity))} "
            f"@ {display_decimal(_text(last.price))}"
        )
    order = next((item for item in window.orders if item.intent_id == classified.intent_id), None)
    if order is None:
        return f"Exit ({reason}): exit intent persisted"
    return (
        f"Exit ({reason}): {order.side.value} {display_decimal(_text(order.quantity))} "
        f"order {order.status.value}"
    )


def _signal_exit_summary(window: _Window, classified: _Classified, matched: str) -> str:
    """``Exit (signal): EMA(20) 101 crosses below EMA(50) 102 → sell 0.01 @ 61000``."""
    fills = [
        fill
        for fill in window.fills
        if (order := _order_for(fill, window)) is not None
        and order.intent_id == classified.intent_id
    ]
    if fills:
        last = fills[-1]
        quantity = sum((fill.quantity for fill in fills), start=Decimal(0))
        order = _order_for(last, window)
        side = order.side.value if order is not None else "exit"
        return (
            f"Exit (signal): {matched} → {side} {display_decimal(_text(quantity))} "
            f"@ {display_decimal(_text(last.price))}"
        )
    order = next((item for item in window.orders if item.intent_id == classified.intent_id), None)
    if order is not None and classified.intent_id is not None:
        return (
            f"Exit (signal): {matched} → {order.side.value} "
            f"{display_decimal(_text(order.quantity))} order {order.status.value}"
        )
    return f"Exit (signal): {matched} → {classified.detail or 'exit in progress'}"


def _holding_summary(
    context: BarContext,
    window: _Window,
    rule: EntryRuleTrace | None,
    exit_rule: ExitRuleTrace | None = None,
) -> str:
    """``Holding long 0.01 @ 64000 · stop 63000 · target 66000``.

    With an evaluated exit rule the line ends with its first unmet leaf, e.g.
    ``· exit rule: EMA(20) 105 needs < EMA(50) 103``.
    """
    position = (context.after or context.before).position
    if position is None:
        return "Holding"
    text = (
        f"Holding {position.side.value} {display_decimal(_text(position.quantity))} "
        f"@ {display_decimal(_text(position.entry_price))} · stop "
        f"{display_decimal(_text(position.stop_price))} · "
        + (
            "no take-profit"
            if position.target_price is None
            else f"target {display_decimal(_text(position.target_price))}"
        )
    )
    entry_fill = next(
        (
            fill
            for fill in window.fills
            if (order := _order_for(fill, window)) is not None
            and _purpose(order, window) is IntentPurpose.ENTRY
        ),
        None,
    )
    if entry_fill is not None:
        text = f"{text} · entry filled @ {display_decimal(_text(entry_fill.price))}"
    observations = context.observations
    if rule is not None and observations is not None and observations.entry_block_code:
        text = f"{text} · signal matched; {observations.entry_block_detail}"
    if exit_rule is not None:
        node = first_unmet(exit_rule.condition)
        if node is not None:
            text = f"{text} · exit rule: {unmet_text(node)}"
    return text


def _skip_summary(
    context: BarContext,
    window: _Window,
    rule: EntryRuleTrace | None,
    classified: _Classified,
) -> str:
    """Name why the bar was skipped, with the counters that explain it."""
    reason = classified.skip_reason
    deployment = (context.after or context.before).deployment
    if reason is DecisionSkipReason.PENDING_ENTRY:
        return _pending_summary(context, window, classified)
    if reason is DecisionSkipReason.WARMUP:
        node = first_unmet(rule.entry) if rule is not None else None
        if node is not None:
            return f"Skipped: warming up — {unmet_text(node)}"
        return "Skipped: warming up (not enough closed bars yet)"
    if reason is DecisionSkipReason.COOLDOWN:
        left = deployment.cooldown_bars_remaining
        return f"Skipped: cooldown ({left} bar(s) left after the last exit)"
    if reason is DecisionSkipReason.MAX_OPEN_POSITIONS:
        limit = context.strategy.portfolio_limits.max_concurrent_positions
        return f"Skipped: max concurrent positions reached ({limit})"
    if reason in {DecisionSkipReason.ENTRY_GEOMETRY, DecisionSkipReason.ENTRY_SIZING}:
        return f"Skipped: signal matched but {classified.detail}"
    return _status_skip_summary(deployment, classified)


def _pending_summary(context: BarContext, window: _Window, classified: _Classified) -> str:
    """Working, repriced, or canceled entry orders."""
    if classified.action is DecisionAction.REPRICED:
        order = next(
            (item for item in window.orders if item.intent_id == classified.intent_id), None
        )
        price = display_decimal(_text(order.price)) if order is not None else "a new price"
        return f"Waiting: repriced the unfilled entry to {price}"
    if classified.action is DecisionAction.ORDER_CANCELED:
        return "Waiting: canceled the unfilled entry order"
    deployment = (context.after or context.before).deployment
    limit = context.strategy.execution.max_entry_wait_bars
    return f"Waiting: entry order working ({deployment.pending_entry_bars} of {limit} bars)"


def _status_skip_summary(deployment: Deployment, classified: _Classified) -> str:
    """Pause, stop, gating, and catch-up skips."""
    reason = classified.skip_reason
    if reason is DecisionSkipReason.PAUSED:
        detail = classified.detail or deployment.mismatch_detail or "paused by the operator"
        return f"Skipped: paused — {detail}"
    if reason is DecisionSkipReason.STOPPED:
        if classified.action is DecisionAction.ORDER_CANCELED:
            return "Skipped: bot stopped — canceled a working entry order"
        return "Skipped: bot stopped"
    if reason is DecisionSkipReason.ENTRIES_DISABLED:
        return "Skipped: new entries are disabled for this bot"
    if reason is DecisionSkipReason.CATCH_UP:
        return "Skipped: catch-up bar after downtime (entries only on the newest bar)"
    if reason is DecisionSkipReason.DATA_GAP:
        return f"Skipped: market data gapped or missing — {classified.detail}".rstrip(" —")
    if reason is DecisionSkipReason.USER_FEED_GATE:
        return "Skipped: the live user-order feed is not connected"
    return "Skipped: entry rule not evaluated on this bar"


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


def _text(value: Decimal | None) -> str | None:
    """Canonical Decimal text, or None."""
    return None if value is None else canonical_decimal(value)
