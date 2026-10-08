"""One-line human summaries for classified bar decisions.

Writes the bounded summary line per outcome, e.g. ``No trade: RSI(14) 47.21 needs >= 50``
or ``Exit (stop): sell 0.01 @ 63000``, from the classified outcome, the decision
window, and the evaluated entry and exit rule traces.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.decision_context import (
    BarContext,
    _Classified,
    _order_for,
    _purpose,
    _text,
    _Window,
)
from thytrader.execution.decision_rules import (
    count_unmet_leaves,
    display_decimal,
    first_unmet,
    met_text,
    unmet_text,
)
from thytrader.execution.decisions import (
    ConditionResult,
    DecisionAction,
    DecisionExitReason,
    DecisionOutcome,
    DecisionSkipReason,
)
from thytrader.trading.models import IntentPurpose

if TYPE_CHECKING:
    from thytrader.execution.decisions import EntryRuleTrace, ExitRuleTrace
    from thytrader.trading.models import Deployment


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
    if reason is DecisionSkipReason.REFERENCE_DATA_STALE:
        return f"Skipped: reference data stale — {classified.detail}".rstrip(" —")
    if reason is DecisionSkipReason.REFERENCE_DATA_MISSING:
        return f"Skipped: reference data missing — {classified.detail}".rstrip(" —")
    return "Skipped: entry rule not evaluated on this bar"
