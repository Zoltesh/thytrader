"""Builder classification edge cases from synthetic before/after snapshots (ADR 0087)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from tests.execution.decision_support import strategy
from thytrader.execution.decision_builder import (
    USER_FEED_GATE_DETAIL,
    BarContext,
    build_bar_decision,
    build_gate_skip_decision,
    pause_skip_reason,
)
from thytrader.execution.decision_scope import DecisionObservations
from thytrader.execution.decisions import (
    DecisionAction,
    DecisionExitReason,
    DecisionOutcome,
    DecisionSkipReason,
)
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Fill,
    IntentPurpose,
    LifecycleCommand,
    Order,
    OrderIntent,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    RuntimePhase,
)
from thytrader.execution.signals import LatestEntryEvaluation
from thytrader.execution_worker.service import USER_FEED_PAUSE_DETAIL
from thytrader.research.trace import EntryConditionOutcome
from thytrader.risk.models import RiskDecision, RiskReasonCode, RiskVerdict

_BAR = datetime(2026, 3, 4, 10, tzinfo=UTC)
_DEFINITION = strategy()


def _deployment(
    *,
    mode: DeploymentMode = DeploymentMode.LIVE,
    status: DeploymentStatus = DeploymentStatus.RUNNING,
    phase: RuntimePhase = RuntimePhase.FLAT,
    lifecycle_command: LifecycleCommand = LifecycleCommand.NONE,
    cooldown_bars_remaining: int = 0,
) -> Deployment:
    """One strategy deployment on BTC-USD 1h (live and running by default)."""
    return Deployment(
        id=UUID("01a0fb09-0000-7000-8000-000000000001"),
        strategy_fingerprint=f"sha256:{'a' * 64}",
        strategy_id=_DEFINITION.strategy_id,
        product_id="BTC-USD",
        mode=mode,
        status=status,
        cash=Decimal("1000"),
        phase=phase,
        created_at=_BAR - timedelta(days=1),
        updated_at=_BAR,
        timeframe="1h",
        lifecycle_command=lifecycle_command,
        cooldown_bars_remaining=cooldown_bars_remaining,
    )


def _position(*, trail_extreme: Decimal | None = None) -> Position:
    """One long book with stop 95 and target 110."""
    return Position(
        deployment_id=_deployment().id,
        quantity=Decimal("0.5"),
        entry_price=Decimal("100"),
        stop_price=Decimal("95"),
        target_price=Decimal("110"),
        entered_bar=_BAR - timedelta(hours=3),
        updated_at=_BAR,
        product_id="BTC-USD",
        trail_extreme=trail_extreme,
    )


def _intent(purpose: IntentPurpose, side: OrderSide = OrderSide.SELL) -> OrderIntent:
    """One persisted intent for the deployment."""
    return OrderIntent(
        id=uuid4(),
        deployment_id=_deployment().id,
        client_order_id=f"c-{uuid4().hex[:12]}",
        purpose=purpose,
        side=side,
        kind=OrderKind.TRIGGER_BRACKET,
        quantity=Decimal("0.5"),
        created_at=_BAR,
        candle_starts_at=_BAR,
        product_id="BTC-USD",
    )


def _order(
    intent: OrderIntent,
    status: OrderStatus,
    *,
    kind: OrderKind | None = None,
    side: OrderSide | None = None,
    price: Decimal = Decimal("110"),
    parent_order_id: UUID | None = None,
) -> Order:
    """One order for an intent (bracket levels stop 95 / target 110)."""
    return Order(
        id=uuid4(),
        deployment_id=intent.deployment_id,
        intent_id=intent.id,
        client_order_id=intent.client_order_id,
        side=side or intent.side,
        kind=kind or intent.kind,
        quantity=intent.quantity,
        status=status,
        created_at=_BAR,
        updated_at=_BAR,
        price=price,
        stop_trigger_price=Decimal("95"),
        take_profit_price=Decimal("110"),
        product_id="BTC-USD",
        parent_order_id=parent_order_id,
    )


def _fill(order: Order, *, price: str, applied_at: datetime) -> Fill:
    """One applied fill for an order."""
    return Fill(
        id=uuid4(),
        deployment_id=order.deployment_id,
        order_id=order.id,
        venue_fill_id=f"v-{uuid4().hex[:8]}",
        price=Decimal(price),
        quantity=Decimal("0.5"),
        fee=Decimal("0.01"),
        filled_at=applied_at,
        economics_applied_at=applied_at,
    )


def _context(
    before: DeploymentSnapshot,
    after: DeploymentSnapshot | None,
    *,
    observations: DecisionObservations | None = None,
    previous_evaluated_at: datetime | None = None,
    error: str | None = None,
) -> BarContext:
    """One bar context on the fixed bar."""
    return BarContext(
        strategy=_DEFINITION,
        product_id="BTC-USD",
        bar_starts_at=_BAR,
        close_price=Decimal("101"),
        evaluated_at=_BAR + timedelta(hours=1, seconds=5),
        allow_new_entries=True,
        before=before,
        after=after,
        observations=observations or DecisionObservations(),
        previous_evaluated_at=previous_evaluated_at,
        error=error,
    )


def test_venue_bracket_fill_between_bars_is_this_bars_exit() -> None:
    """A live OCO stop leg reconciled between bars is attributed to the next decision."""
    intent = _intent(IntentPurpose.BRACKET)
    order = _order(intent, OrderStatus.FILLED)
    fill = _fill(order, price="95", applied_at=_BAR + timedelta(minutes=20))
    snapshot = DeploymentSnapshot(
        deployment=_deployment(),
        orders=(order,),
        fills=(fill,),
        intents=(intent,),
    )
    decision = build_bar_decision(
        _context(snapshot, snapshot, previous_evaluated_at=_BAR + timedelta(seconds=5))
    )
    assert decision.outcome is DecisionOutcome.EXIT
    assert decision.exit_reason is DecisionExitReason.STOP
    assert decision.action is DecisionAction.NONE
    assert decision.intent_id == intent.id
    assert [item.fill_id for item in decision.fills] == [fill.id]
    assert decision.summary == "Exit (stop): sell 0.5 @ 95"


def test_fills_already_journaled_by_the_previous_decision_are_not_repeated() -> None:
    """A fill applied before the previous decision was written belongs to that bar."""
    intent = _intent(IntentPurpose.BRACKET)
    order = _order(intent, OrderStatus.FILLED)
    fill = _fill(order, price="95", applied_at=_BAR - timedelta(minutes=30))
    snapshot = DeploymentSnapshot(
        deployment=_deployment(), orders=(order,), fills=(fill,), intents=(intent,)
    )
    decision = build_bar_decision(
        _context(snapshot, snapshot, previous_evaluated_at=_BAR + timedelta(seconds=5))
    )
    assert decision.outcome is not DecisionOutcome.EXIT
    assert decision.fills == ()


def test_attached_child_take_profit_fill_is_an_exit_at_target() -> None:
    """A venue-attached child of the entry (parent order set) acts as the bracket."""
    entry = _intent(IntentPurpose.ENTRY, side=OrderSide.BUY)
    parent = _order(entry, OrderStatus.FILLED, side=OrderSide.BUY)
    child = _order(entry, OrderStatus.FILLED, parent_order_id=parent.id)
    fill = _fill(child, price="110", applied_at=_BAR + timedelta(minutes=40))
    snapshot = DeploymentSnapshot(
        deployment=_deployment(), orders=(parent, child), fills=(fill,), intents=(entry,)
    )
    decision = build_bar_decision(
        _context(snapshot, snapshot, previous_evaluated_at=_BAR + timedelta(seconds=5))
    )
    assert decision.outcome is DecisionOutcome.EXIT
    assert decision.exit_reason is DecisionExitReason.TARGET
    assert {item.purpose for item in decision.fills} == {IntentPurpose.BRACKET}


def test_flatten_command_exit_is_named_flatten() -> None:
    """A marketable STOP intent under a flatten command is ``exit (flatten)``."""
    before = DeploymentSnapshot(
        deployment=_deployment(
            status=DeploymentStatus.STOPPED, lifecycle_command=LifecycleCommand.FLATTEN
        ),
        position=_position(),
    )
    intent = _intent(IntentPurpose.STOP)
    order = _order(intent, OrderStatus.OPEN, kind=OrderKind.MARKETABLE)
    after = replace(before, position=None, intents=(intent,), orders=(order,))
    decision = build_bar_decision(_context(before, after))
    assert decision.outcome is DecisionOutcome.EXIT
    assert decision.exit_reason is DecisionExitReason.FLATTEN
    assert decision.action is DecisionAction.ORDER_SUBMITTED
    assert decision.summary == "Exit (flatten): sell 0.5 order open"


def test_trailing_stop_exit_is_named_trail_once_the_stop_ratcheted() -> None:
    """With an ATR trail enabled and ratcheted, a stop exit reads ``exit (trail)``."""
    payload = _DEFINITION.model_dump(mode="python", by_alias=True)
    payload["exits"]["trailing_stop"] = {
        "enabled": True,
        "kind": "atr_multiple",
        "atr_indicator": "atr",
        "multiple": "2",
    }
    trailing = type(_DEFINITION).model_validate(payload)
    before = DeploymentSnapshot(
        deployment=_deployment(mode=DeploymentMode.PAPER),
        position=_position(trail_extreme=Decimal("108")),
    )
    intent = _intent(IntentPurpose.STOP)
    order = _order(intent, OrderStatus.FILLED, kind=OrderKind.MARKETABLE)
    after = replace(before, position=None, intents=(intent,), orders=(order,))
    context = replace(_context(before, after), strategy=trailing)
    decision = build_bar_decision(context)
    assert decision.exit_reason is DecisionExitReason.TRAIL
    assert decision.reason_code == "EXIT_TRAIL"


def test_repriced_working_entry_is_a_pending_entry_skip_with_repriced_action() -> None:
    """A replacement entry without a rule evaluation is a reprice, not a new signal."""
    before = DeploymentSnapshot(deployment=_deployment(phase=RuntimePhase.PENDING_ENTRY))
    intent = _intent(IntentPurpose.ENTRY, side=OrderSide.BUY)
    order = _order(intent, OrderStatus.OPEN, kind=OrderKind.POST_ONLY_LIMIT, price=Decimal("99"))
    after = replace(before, intents=(intent,), orders=(order,))
    decision = build_bar_decision(_context(before, after))
    assert decision.outcome is DecisionOutcome.SKIPPED
    assert decision.skip_reason is DecisionSkipReason.PENDING_ENTRY
    assert decision.action is DecisionAction.REPRICED
    assert decision.summary == "Waiting: repriced the unfilled entry to 99"


def test_canceled_unfilled_entry_is_a_pending_entry_skip_with_cancel_action() -> None:
    """An abandoned working entry records the canceled order."""
    intent = _intent(IntentPurpose.ENTRY, side=OrderSide.BUY)
    working = _order(intent, OrderStatus.OPEN, kind=OrderKind.POST_ONLY_LIMIT)
    before = DeploymentSnapshot(
        deployment=_deployment(phase=RuntimePhase.PENDING_ENTRY),
        intents=(intent,),
        orders=(working,),
    )
    after = replace(
        before,
        deployment=_deployment(cooldown_bars_remaining=3),
        orders=(replace(working, status=OrderStatus.CANCELED),),
    )
    decision = build_bar_decision(_context(before, after))
    assert decision.outcome is DecisionOutcome.SKIPPED
    assert decision.action is DecisionAction.ORDER_CANCELED
    assert decision.intent_id == intent.id
    assert [item.status for item in decision.orders] == [OrderStatus.CANCELED]


def test_unconfirmed_entry_submit_is_intent_created() -> None:
    """A matched signal whose submit is unknown only persisted its intent."""
    before = DeploymentSnapshot(deployment=_deployment())
    intent = _intent(IntentPurpose.ENTRY, side=OrderSide.BUY)
    order = _order(intent, OrderStatus.UNKNOWN, kind=OrderKind.POST_ONLY_LIMIT)
    after = replace(before, intents=(intent,), orders=(order,))
    observations = DecisionObservations()
    observations.evaluation = LatestEntryEvaluation(
        outcome=EntryConditionOutcome.MATCHED,
        candle_starts_at=_BAR,
        ltf_outcome=EntryConditionOutcome.MATCHED,
        current={"rsi": Decimal("61.5")},
        previous={"rsi": Decimal("58")},
    )
    decision = build_bar_decision(_context(before, after, observations=observations))
    assert decision.outcome is DecisionOutcome.ENTRY_SIGNAL
    assert decision.action is DecisionAction.INTENT_CREATED
    assert decision.summary == "Entry: RSI(14) 61.5 ≥ 50 → buy 0.5 @ 110 (unknown)"


def test_runtime_breaker_pause_is_a_paused_skip_with_the_verdict() -> None:
    """A tripped breaker pauses entries; the bar carries the risk verdict."""
    observations = DecisionObservations()
    observations.breaker = RiskVerdict(
        decision=RiskDecision.DENY,
        reason_code=RiskReasonCode.DAILY_LOSS_LIMIT,
        detail="UTC-day loss reached the configured limit.",
    )
    before = DeploymentSnapshot(deployment=_deployment())
    after = replace(before, deployment=_deployment(status=DeploymentStatus.PAUSED))
    decision = build_bar_decision(_context(before, after, observations=observations))
    assert decision.outcome is DecisionOutcome.SKIPPED
    assert decision.skip_reason is DecisionSkipReason.PAUSED
    assert decision.risk is not None
    assert decision.risk.reason_code == "DAILY_LOSS_LIMIT"
    assert decision.summary == "Skipped: paused — UTC-day loss reached the configured limit."


def test_breaker_tripped_while_holding_keeps_the_verdict_on_the_holding_bar() -> None:
    """A holding bar that tripped a breaker still records the risk verdict."""
    observations = DecisionObservations()
    observations.breaker = RiskVerdict(
        decision=RiskDecision.DENY,
        reason_code=RiskReasonCode.STRATEGY_DRAWDOWN_LIMIT,
        detail="Strategy drawdown reached the configured limit.",
    )
    before = DeploymentSnapshot(deployment=_deployment(), position=_position())
    after = replace(before, deployment=_deployment(status=DeploymentStatus.PAUSED))
    decision = build_bar_decision(_context(before, after, observations=observations))
    assert decision.outcome is DecisionOutcome.HOLDING
    assert decision.risk is not None
    assert decision.risk.reason_code == "STRATEGY_DRAWDOWN_LIMIT"


def test_raised_cycle_is_an_error_bar() -> None:
    """A closed-bar call that raised journals ``error`` without an after snapshot."""
    before = DeploymentSnapshot(deployment=_deployment())
    decision = build_bar_decision(_context(before, None, error="processing raised RuntimeError"))
    assert decision.outcome is DecisionOutcome.ERROR
    assert decision.reason_code == "CYCLE_ERROR"
    assert decision.summary == "Error: processing raised RuntimeError"


def test_gate_skips_name_data_gaps_and_the_user_feed_gate() -> None:
    """Bars the worker could not evaluate are journaled with the gate and the book."""
    snapshot = DeploymentSnapshot(
        deployment=_deployment(status=DeploymentStatus.PAUSED), position=_position()
    )
    gap = build_gate_skip_decision(
        snapshot=snapshot,
        strategy=_DEFINITION,
        product_id="BTC-USD",
        bar_starts_at=_BAR,
        evaluated_at=_BAR + timedelta(hours=1),
        reason=DecisionSkipReason.DATA_GAP,
        detail="Market-data window is gapped or missing the latest closed bar.",
    )
    assert gap.outcome is DecisionOutcome.SKIPPED
    assert gap.reason_code == "DATA_GAP"
    assert gap.summary == (
        "Skipped: market data gapped or missing — "
        "Market-data window is gapped or missing the latest closed bar."
    )
    assert gap.position is not None
    assert gap.bar_closes_at == _BAR + timedelta(hours=1)
    feed = build_gate_skip_decision(
        snapshot=snapshot,
        strategy=_DEFINITION,
        product_id="BTC-USD",
        bar_starts_at=_BAR,
        evaluated_at=_BAR + timedelta(hours=1),
        reason=DecisionSkipReason.USER_FEED_GATE,
        detail=USER_FEED_GATE_DETAIL,
    )
    assert feed.summary == "Skipped: the live user-order feed is not connected"


def test_pause_details_map_onto_their_gates() -> None:
    """Worker pause details classify as feed gate, data gap, or a plain pause."""
    assert USER_FEED_GATE_DETAIL == USER_FEED_PAUSE_DETAIL
    assert pause_skip_reason(USER_FEED_PAUSE_DETAIL) is DecisionSkipReason.USER_FEED_GATE
    assert (
        pause_skip_reason(
            "HTF market-data window is gapped or missing the latest completed HTF bar."
        )
        is DecisionSkipReason.DATA_GAP
    )
    assert pause_skip_reason("Entry submit is unconfirmed.") is DecisionSkipReason.PAUSED


def test_a_no_trade_bar_is_evaluated_and_named_on_its_record() -> None:
    """ADR 0095: a flat zero-volume bar is a normal decision whose record names it."""
    intent = _intent(IntentPurpose.BRACKET)
    order = _order(intent, OrderStatus.FILLED)
    fill = _fill(order, price="95", applied_at=_BAR + timedelta(minutes=20))
    snapshot = DeploymentSnapshot(
        deployment=_deployment(),
        orders=(order,),
        fills=(fill,),
        intents=(intent,),
    )
    context = _context(snapshot, snapshot, previous_evaluated_at=_BAR + timedelta(seconds=5))

    plain = build_bar_decision(context)
    flat = build_bar_decision(replace(context, no_trade_bar=True))

    assert plain.no_trade_bar is False
    assert flat.no_trade_bar is True
    assert flat.outcome is plain.outcome is DecisionOutcome.EXIT
    assert (
        flat.summary
        == "Exit (stop): sell 0.5 @ 95 (no-trade bar: no trades, flat at the prior close)"
    )
