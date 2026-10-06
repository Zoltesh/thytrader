"""One closed-candle cycle for a deployed strategy."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import inspect
from itertools import pairwise
from typing import TYPE_CHECKING, Literal

from thytrader.execution.attached import (
    attached_entry_covers as _attached_entry_covers,
    filled_attached_entry,
    remaining_quantity,
)
from thytrader.execution.audit_scope import record_execution_audit
from thytrader.execution.broker import CANCEL_PENDING_REASON, BrokerError
from thytrader.execution.capital import live_capital_base, live_sizing_cash, refresh_performance
from thytrader.execution.decision_scope import (
    decision_observation_active,
    note_breaker,
    note_entry_block,
    note_entry_gate,
    note_entry_skip,
    note_evaluation,
    note_evaluation_error,
    note_exit_evaluation,
    note_freshness,
    note_risk,
)
from thytrader.execution.decisions import DecisionSkipReason
from thytrader.execution.exit_guards import (
    BRACKET_NOT_RESTED_DETAIL,
    BRACKET_REPLACE_CANCEL_DETAIL,
    BRACKET_UNCONFIRMED_DETAIL,
    CANCEL_BEFORE_EXIT_DETAIL,
    FLAT_AFTER_FAULT_DETAIL,
    MARKETABLE_EXIT_UNCONFIRMED_DETAIL,
    SubmitRejection,
    active_orders,
    bracket_rejection,
    cancel_pending,
    exit_fill_pending,
    exit_rejection,
    flat_and_idle,
    flatten_requested,
    rejection_detail,
    rejection_latched,
    stale_position_fault,
)
from thytrader.execution.fill_ledger import (
    applied_fill_quantity,
    ingest_fill,
    prior_fills_for_order,
)
from thytrader.execution.freshness import entry_prerequisites, signal_still_valid
from thytrader.execution.geometry import (
    EntrySkipReason,
    entry_bar_bucket,
    entry_order_side,
    exit_order_side,
    paper_stop_fill_price,
    paper_stop_hit,
    protective_stop_limit_price,
)
from thytrader.execution.ids import utc_now
from thytrader.execution.ledger import PAPER_MAKER_FEE_RATE, effective_paper_fee_rates
from thytrader.execution.lifecycle import can_reprice_risk_up, entries_allowed
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    Fill,
    IntentPurpose,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    Position,
    PositionSide,
    RuntimePhase,
    is_venue_protection,
    resolved_product_id,
    snapshot_positions,
    with_runtime,
)
from thytrader.execution.paper import bind_paper_broker_fees
from thytrader.execution.reconcile import (
    FILLED_WITHOUT_REST_FILLS_DETAIL,
    import_attached_children,
    ingest_order_fills,
    reconcile_open_orders,
)
from thytrader.execution.signals import (
    evaluate_latest_entry_evidence,
    evaluate_latest_signal_exit,
    latest_atr,
    named_atr,
)
from thytrader.execution.sizing import (
    SizedEntry,
    size_entry_or_skip,
    size_pyramid_add_or_skip,
)
from thytrader.execution.submit import submit_intent
from thytrader.execution.trade_reason_scope import current_trade_reason_scope
from thytrader.execution.trailing import ratcheted_long_stop, ratcheted_short_stop
from thytrader.market_data.models import parse_candle_interval
from thytrader.persistence.audit_events import AuditEventOutcome
from thytrader.research.multi_timeframe import htf_bars_closed_at_or_before, ltf_close
from thytrader.research.signal_evaluator import SignalEvaluationError
from thytrader.research.trace import EntryConditionOutcome
from thytrader.risk.breakers import EntryObservation, breaker_pause_detail
from thytrader.risk.exposure import snapshot_has_residual_exposure
from thytrader.risk.gate import ProposedEntry, evaluate_new_entry, evaluate_runtime_breakers
from thytrader.risk.models import (
    RiskDecision,
    RiskReasonCode,
    RiskVerdict,
    compiled_default_risk_policy,
    pauses_risk_increasing,
)
from thytrader.risk.portfolio_scope import portfolio_risk_for
from thytrader.strategies.models import (
    atr_trailing_stop,
    can_pyramid_add,
    signal_exit_condition,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime
    from uuid import UUID

    from thytrader.exchanges.fees import FeeProfile
    from thytrader.execution.broker import Broker
    from thytrader.execution.store import ExecutionStore
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.strategies.models import StrategyDefinition

_ACTIVE = {OrderStatus.OPEN, OrderStatus.PENDING, OrderStatus.UNKNOWN}
_IN_MARKET = {RuntimePhase.OPEN, RuntimePhase.PENDING_EXIT}
STOP_LIMIT_PRICE_DETAIL = (
    "Stop-only protection limit price is not positive at the venue price increment."
)
_STALE_SIGNAL_VERDICT = RiskVerdict(
    decision=RiskDecision.DENY,
    reason_code=RiskReasonCode.SIGNAL_STALE,
    detail="The closed signal bar is older than the maximum signal age; no entry was sent.",
)


async def maintain_open_inventory(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    product: MarketProduct,
    candles: Sequence[Candle],
    broker: Broker,
    store: ExecutionStore,
    htf_candles: Sequence[Candle] = (),
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None = None,
    reference_candles: Mapping[str, Sequence[Candle]] | None = None,
) -> DeploymentSnapshot:
    """Reconcile resting orders and ensure protection between closed bars.

    ``htf_candles`` / ``indicator_timeframe_candles`` / ``reference_candles`` are only
    needed when a newly closed bar is processed here and the ``exits.signal_exit`` rule
    reads extra-TF or reference-instrument indicators (ADR 0093, ADR 0096).
    """
    if not candles:
        return snapshot
    return await process_closed_bar(
        snapshot,
        strategy=strategy,
        product=product,
        candles=candles,
        broker=bind_paper_broker_fees(broker, snapshot.deployment),
        store=store,
        htf_candles=htf_candles,
        indicator_timeframe_candles=indicator_timeframe_candles,
        reference_candles=reference_candles,
        allow_new_entries=False,
    )


FLATTEN_AWAITING_EXECUTABLE_CONTEXT = (
    "Flatten is pending: no verified closed price is available, so protective orders "
    "were kept and no exit was submitted."
)
"""Operator detail when flatten cannot exit without inventing a price."""


async def flatten_stopped_residual(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    product: MarketProduct,
    candles: Sequence[Candle],
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Marketably exit open inventory on a flatten command, then cancel remainders.

    Protective children are cancelled before the exit (``_marketable_exit``); once the
    book is flat with nothing working it settles as STOPPED/FLAT with no stale detail.
    Without a verified candle the position is not exited and protection is not cancelled.
    """
    return await flatten_residual_book(
        snapshot,
        strategy=strategy,
        product=product,
        candles=candles,
        broker=broker,
        store=store,
        cooldown_bars=strategy.entry.cooldown_bars,
    )


async def flatten_discretionary_residual(
    snapshot: DeploymentSnapshot,
    *,
    product: MarketProduct,
    candles: Sequence[Candle],
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Flatten one stopped discretionary book without a strategy snapshot."""
    return await flatten_residual_book(
        snapshot,
        product=product,
        candles=candles,
        broker=broker,
        store=store,
        cooldown_bars=0,
    )


async def flatten_residual_book(
    snapshot: DeploymentSnapshot,
    *,
    product: MarketProduct,
    candles: Sequence[Candle],
    broker: Broker,
    store: ExecutionStore,
    strategy: StrategyDefinition | None = None,
    cooldown_bars: int = 0,
) -> DeploymentSnapshot:
    """Exit one book only after cancels and live fills are known.

    An empty candle sequence never submits an exit and never cancels protection.
    A cancel that races a fill is reconciled before another exit is sent. An
    unconfirmed cancel stays supervised and is not treated as success.
    """
    broker = bind_paper_broker_fees(broker, snapshot.deployment)
    if snapshot.position is not None and not candles:
        return await defer_flatten_without_executable_context(snapshot, broker=broker, store=store)
    if snapshot.position is None:
        return await _cancel_and_settle(snapshot, broker=broker, store=store)
    return await _exit_after_confirmed_cancels(
        snapshot,
        strategy=strategy,
        product=product,
        candles=candles,
        broker=broker,
        store=store,
        cooldown_bars=cooldown_bars,
    )


async def defer_flatten_without_executable_context(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Keep protection and record that flatten has no verified exit price.

    Only orders whose intent is an entry may be cancelled. Venue brackets,
    stop-limits, and take-profit orders stay so the book is not left naked.
    """
    snapshot = await _cancel_open_entries(snapshot, broker=broker, store=store)
    current = await _reconcile_stopped_live(
        await store.get_deployment(snapshot.deployment.id), broker=broker, store=store
    )
    if current.deployment.mismatch_detail is not None:
        # Do not hide a genuine reconciliation/cancellation fault behind a data wait.
        return current
    noted = with_runtime(
        current.deployment,
        updated_at=utc_now(),
        status=current.deployment.status,
        mismatch_detail=FLATTEN_AWAITING_EXECUTABLE_CONTEXT,
    )
    await store.save_deployment(noted)
    await record_execution_audit(
        action="flatten_awaiting_price",
        outcome=AuditEventOutcome.FAILURE,
        detail=(
            f"deployment_id={current.deployment.id}: flatten has no verified closed price; "
            "protective orders were kept and no exit was submitted."
        ),
        product_id=current.position.product_id if current.position is not None else None,
    )
    return await store.get_deployment(current.deployment.id)


async def maintain_discretionary_protection(
    snapshot: DeploymentSnapshot,
    *,
    product: MarketProduct,
    candles: Sequence[Candle],
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Keep a discretionary stop or target working, including after shutdown.

    Missing candles do nothing: a shutdown must not invent an exit price or
    cancel protection merely because the decision window is empty.
    """
    if not candles or snapshot.position is None:
        return snapshot
    broker = bind_paper_broker_fees(broker, snapshot.deployment)
    candle = candles[-1]
    if snapshot.deployment.mode is not DeploymentMode.LIVE:
        snapshot = await _match_resting_orders(
            snapshot,
            candle=candle,
            broker=broker,
            store=store,
            cooldown_bars=0,
            stop_first=True,
        )
    position = snapshot.position
    if position is None:
        return snapshot
    if snapshot.deployment.mode is DeploymentMode.LIVE:
        return await _ensure_live_bracket(
            snapshot, candle=candle, product=product, broker=broker, store=store
        )
    if paper_stop_hit(side=position.side, candle=candle, stop_price=position.stop_price):
        return await _marketable_exit(
            snapshot,
            candle=candle,
            product=product,
            broker=broker,
            store=store,
            purpose=IntentPurpose.STOP,
            price=paper_stop_fill_price(
                side=position.side, candle=candle, stop_price=position.stop_price
            ),
            cooldown_bars=0,
        )
    return await _ensure_take_profit(
        snapshot, candle=candle, product=product, broker=broker, store=store
    )


async def _cancel_and_settle(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Cancel remainders on a book that no longer has inventory, then settle."""
    cleared = await cancel_resting_orders(snapshot, broker=broker, store=store)
    cleared = await _reconcile_stopped_live(cleared, broker=broker, store=store)
    return await _settle_flat_book(cleared, store=store)


async def _exit_after_confirmed_cancels(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition | None,
    product: MarketProduct,
    candles: Sequence[Candle],
    broker: Broker,
    store: ExecutionStore,
    cooldown_bars: int,
) -> DeploymentSnapshot:
    """Cancel resting orders, learn any racing fill, then exit only if still open."""
    candle = candles[-1]
    exited = await _marketable_exit(
        snapshot,
        strategy=strategy,
        cooldown_bars=cooldown_bars,
        candle=candle,
        product=product,
        broker=broker,
        store=store,
        purpose=IntentPurpose.STOP,
        price=candle.close,
    )
    if exited.position is not None:
        return exited
    return await _cancel_and_settle(exited, broker=broker, store=store)


async def _reconcile_stopped_live(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Apply live fills before a flatten decision; paper books stay local."""
    if snapshot.deployment.mode is not DeploymentMode.LIVE:
        return snapshot
    return await reconcile_open_orders(
        snapshot,
        broker=broker,
        store=store,
        product_id=snapshot.position.product_id if snapshot.position is not None else None,
        cooldown_bars=0,
    )


async def _cancel_open_entries(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Cancel working entries and leave protective orders untouched."""
    entry_ids = {intent.id for intent in snapshot.intents if intent.purpose is IntentPurpose.ENTRY}
    for order in tuple(snapshot.orders):
        if order.status not in _ACTIVE or order.intent_id not in entry_ids:
            continue
        if is_venue_protection(order.kind):
            continue
        snapshot = await _cancel_one_order(order, broker=broker, store=store)
    return await store.get_deployment(snapshot.deployment.id)


def _exit_cooldown(strategy: StrategyDefinition | None, cooldown_bars: int | None) -> int:
    """Use an explicit cooldown, else the strategy's, else zero for discretionary books."""
    if cooldown_bars is not None:
        return cooldown_bars
    if strategy is None:
        return 0
    return strategy.entry.cooldown_bars


async def cancel_risk_increasing_orders(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Cancel working entries while leaving protective brackets and stop-limits in place."""
    entry_ids = {intent.id for intent in snapshot.intents if intent.purpose is IntentPurpose.ENTRY}
    for order in tuple(snapshot.orders):
        if order.status not in _ACTIVE:
            continue
        if is_venue_protection(order.kind):
            continue
        if entry_ids and order.intent_id not in entry_ids:
            continue
        snapshot = await _cancel_one_order(order, broker=broker, store=store)
    return await store.get_deployment(snapshot.deployment.id)


async def process_closed_bar(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    product: MarketProduct,
    candles: Sequence[Candle],
    broker: Broker,
    store: ExecutionStore,
    risk_policy: RiskPolicyDefinition | None = None,
    portfolio: Sequence[DeploymentSnapshot] = (),
    htf_candles: Sequence[Candle] = (),
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None = None,
    reference_candles: Mapping[str, Sequence[Candle]] | None = None,
    live_base_available: Decimal | None = None,
    marks: Mapping[str, Decimal] | None = None,
    fee_profile: FeeProfile | None = None,
    allow_new_entries: bool = True,
) -> DeploymentSnapshot:
    """Advance one running, paused, or residual-stopped deployment by one closed candle."""
    if not candles:
        return snapshot
    stopped = snapshot.deployment.status is DeploymentStatus.STOPPED
    if stopped and not snapshot_has_residual_exposure(snapshot):
        return snapshot
    if snapshot.deployment.status not in {
        DeploymentStatus.RUNNING,
        DeploymentStatus.PAUSED,
        DeploymentStatus.STOPPED,
    }:
        return snapshot
    broker = bind_paper_broker_fees(broker, snapshot.deployment)
    candle = candles[-1]
    deployment = snapshot.deployment
    if deployment.last_evaluated_bar == candle.starts_at:
        if deployment.phase in _IN_MARKET or snapshot.position is not None:
            snapshot = await _ensure_exit_protection(
                snapshot,
                candle=candle,
                product=product,
                broker=broker,
                store=store,
                strategy=strategy,
            )
        snapshot = await _persist_performance(
            snapshot,
            store=store,
            mark_price=candle.close,
            marks=marks,
            product_id=product.product_id,
        )
        return await _settle_flat_book(snapshot, store=store)
    if deployment.cooldown_bars_remaining > 0 and not stopped:
        cooled = with_runtime(
            deployment,
            updated_at=utc_now(),
            cooldown_bars_remaining=deployment.cooldown_bars_remaining - 1,
        )
        await store.save_deployment(cooled)
        snapshot = await store.get_deployment(deployment.id)
    policy = risk_policy or compiled_default_risk_policy()
    snapshot = await _match_resting_orders(
        snapshot,
        candle=candle,
        broker=broker,
        store=store,
        cooldown_bars=strategy.entry.cooldown_bars,
        timeframe=deployment.timeframe or strategy.timeframe,
        stop_first=deployment.mode is not DeploymentMode.LIVE,
    )
    snapshot = await _manage_position(
        snapshot,
        strategy=strategy,
        candles=candles,
        candle=candle,
        product=product,
        broker=broker,
        store=store,
        marks=marks,
        live_base_available=live_base_available,
        risk_policy=policy,
        portfolio=portfolio,
        htf_candles=htf_candles,
        indicator_timeframe_candles=indicator_timeframe_candles,
        reference_candles=reference_candles,
        fee_profile=fee_profile,
    )
    if snapshot.deployment.status is DeploymentStatus.RUNNING:
        snapshot = await _apply_circuit_breakers(
            snapshot,
            candle=candle,
            store=store,
            risk_policy=policy,
            portfolio=portfolio,
            marks=marks,
        )
    may_enter = (
        allow_new_entries
        and entries_allowed(snapshot.deployment)
        and snapshot.deployment.status is DeploymentStatus.RUNNING
    )
    if may_enter:
        snapshot = await _maybe_enter(
            snapshot,
            strategy=strategy,
            product=product,
            candles=candles,
            candle=candle,
            broker=broker,
            store=store,
            risk_policy=policy,
            portfolio=portfolio,
            htf_candles=htf_candles,
            indicator_timeframe_candles=indicator_timeframe_candles,
            reference_candles=reference_candles,
            live_base_available=live_base_available,
            marks=marks,
            fee_profile=fee_profile,
        )
    snapshot = await _persist_performance(
        snapshot,
        store=store,
        mark_price=candle.close,
        marks=marks,
        product_id=product.product_id,
    )
    snapshot = await _persist_runtime(
        snapshot,
        store=store,
        last_evaluated_bar=candle.starts_at,
    )
    return await _settle_flat_book(snapshot, store=store)


async def cancel_resting_orders(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Cancel every locally open order, retaining ambiguous stopped remainders."""
    await _cancel_open_orders(snapshot, broker=broker, store=store)
    current = await store.get_deployment(snapshot.deployment.id)
    blocking = active_orders(current)
    if current.deployment.status is DeploymentStatus.STOPPED and blocking:
        if all(cancel_pending(order) for order in blocking):
            return current
        return await _pause(current, store=store, detail=CANCEL_BEFORE_EXIT_DETAIL)
    return current


async def _match_resting_orders(
    snapshot: DeploymentSnapshot,
    *,
    candle: Candle,
    broker: Broker,
    store: ExecutionStore,
    cooldown_bars: int,
    timeframe: str | None = None,
    stop_first: bool = False,
) -> DeploymentSnapshot:
    """Apply paper or local fills for resting limits against the closed candle.

    With ``stop_first`` (paper), a resting take-profit is not matched on a candle that also
    trades through the position's stop: the candle cannot show which traded first, so the
    stop exit in position management wins (the same rule as the backtest model, ADR 0083).
    """
    for order in snapshot.orders:
        if order.status is not OrderStatus.OPEN:
            continue
        if stop_first and _stop_preempts_take_profit(snapshot, order=order, candle=candle):
            continue
        fill = broker.match_open_order(order, candle)
        if fill is None:
            continue
        result = await ingest_fill(
            snapshot,
            fill=fill,
            order=order,
            store=store,
            cooldown_bars=cooldown_bars,
            timeframe=timeframe,
        )
        snapshot = result.snapshot
    return await store.get_deployment(snapshot.deployment.id)


def _stop_preempts_take_profit(
    snapshot: DeploymentSnapshot, *, order: Order, candle: Candle
) -> bool:
    """Return whether this resting take-profit must yield to a stop the candle also hit."""
    intent = next((item for item in snapshot.intents if item.id == order.intent_id), None)
    if intent is None or intent.purpose is not IntentPurpose.TAKE_PROFIT:
        return False
    position = snapshot.position
    if position is None:
        return False
    if order.product_id and position.product_id and order.product_id != position.product_id:
        return False
    return paper_stop_hit(side=position.side, candle=candle, stop_price=position.stop_price)


async def apply_fill(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    order: Order,
    store: ExecutionStore,
    cooldown_bars: int = 0,
    timeframe: str | None = None,
) -> DeploymentSnapshot:
    """Update cash and position from one fill and persist the result."""
    result = await ingest_fill(
        snapshot,
        fill=fill,
        order=order,
        store=store,
        cooldown_bars=cooldown_bars,
        timeframe=timeframe or snapshot.deployment.timeframe,
    )
    return result.snapshot


async def _apply_entry_fill(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    order: Order,
    store: ExecutionStore,
    timeframe: str | None = None,
) -> DeploymentSnapshot:
    """Open a long from a buy fill or a short from a sell fill."""
    deployment = snapshot.deployment
    now = utc_now()
    side = PositionSide.LONG if order.side is OrderSide.BUY else PositionSide.SHORT
    cash = _cash_after_fill(deployment.cash, fill=fill, order_side=order.side)
    stop = deployment.pending_stop_price
    # A None target is legal: the strategy declares no take-profit (ADR 0090), and the
    # book is protected by its stop alone. The stop is always required.
    target = deployment.pending_target_price
    if stop is None:
        paused = with_runtime(
            deployment,
            updated_at=now,
            cash=cash,
            status=DeploymentStatus.PAUSED,
            mismatch_detail="Entry fill is missing its stored stop price.",
            phase=RuntimePhase.FLAT,
            clear_pending_levels=True,
        )
        await store.save_deployment(paused)
        await store.save_position(None, deployment_id=deployment.id)
        return await store.get_deployment(deployment.id)
    bar_timeframe = timeframe or deployment.timeframe
    if bar_timeframe is None:
        paused = with_runtime(
            deployment,
            updated_at=now,
            cash=cash,
            status=DeploymentStatus.PAUSED,
            mismatch_detail="Entry fill is missing a deployment timeframe for bar bucketing.",
            phase=RuntimePhase.FLAT,
            clear_pending_levels=True,
        )
        await store.save_deployment(paused)
        await store.save_position(None, deployment_id=deployment.id)
        return await store.get_deployment(deployment.id)
    entered_bar = entry_bar_bucket(fill.filled_at, bar_timeframe)
    position = Position(
        deployment_id=deployment.id,
        quantity=fill.quantity,
        entry_price=fill.price,
        stop_price=stop,
        target_price=target,
        entered_bar=entered_bar,
        updated_at=now,
        side=side,
        product_id=order.product_id or deployment.product_id,
        add_count=1,
    )
    updated = with_runtime(
        deployment,
        updated_at=now,
        cash=cash,
        phase=RuntimePhase.OPEN,
        bars_held=0,
        pending_entry_bars=0,
        clear_pending_levels=True,
    )
    await store.save_position(position, deployment_id=deployment.id)
    await store.save_deployment(updated)
    return await store.get_deployment(deployment.id)


async def _apply_scale_in_fill(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    order: Order,
    store: ExecutionStore,
    position: Position,
) -> DeploymentSnapshot:
    """Add to an existing position on the same side."""
    deployment = snapshot.deployment
    now = utc_now()
    cash = _cash_after_fill(deployment.cash, fill=fill, order_side=order.side)
    quantity = position.quantity + fill.quantity
    entry_price = (
        (position.entry_price * position.quantity) + (fill.price * fill.quantity)
    ) / quantity
    prior = prior_fills_for_order(snapshot, order.id)
    add_count = position.add_count + 1 if prior == 0 and order.pyramid_add else position.add_count
    updated_position = replace(
        position,
        quantity=quantity,
        entry_price=entry_price,
        updated_at=now,
        add_count=add_count,
    )
    updated = with_runtime(
        deployment,
        updated_at=now,
        cash=cash,
        phase=RuntimePhase.OPEN if deployment.phase is RuntimePhase.FLAT else deployment.phase,
    )
    await store.save_position(updated_position, deployment_id=deployment.id)
    await store.save_deployment(updated)
    return await store.get_deployment(deployment.id)


async def _apply_exit_fill(
    snapshot: DeploymentSnapshot,
    *,
    fill: Fill,
    order: Order,
    store: ExecutionStore,
    cooldown_bars: int,
) -> DeploymentSnapshot:
    """Reduce or flatten the open position after a covering fill."""
    deployment = snapshot.deployment
    cash = _cash_after_fill(deployment.cash, fill=fill, order_side=order.side)
    position = snapshot.position
    if position is not None and fill.quantity < position.quantity:
        remaining = replace(
            position,
            quantity=position.quantity - fill.quantity,
            updated_at=utc_now(),
        )
        updated = with_runtime(deployment, updated_at=utc_now(), cash=cash)
        await store.save_position(remaining, deployment_id=deployment.id)
        await store.save_deployment(updated)
        return await store.get_deployment(deployment.id)
    updated = with_runtime(
        deployment,
        updated_at=utc_now(),
        cash=cash,
        phase=RuntimePhase.FLAT,
        bars_held=0,
        pending_entry_bars=0,
        cooldown_bars_remaining=max(cooldown_bars, 0),
        clear_pending_levels=True,
    )
    await store.save_position(None, deployment_id=deployment.id)
    await store.save_deployment(updated)
    return await store.get_deployment(deployment.id)


def _cash_after_fill(cash: Decimal, *, fill: Fill, order_side: OrderSide) -> Decimal:
    """Apply quote cash for a spot buy (debit) or sell (credit)."""
    notional = fill.price * fill.quantity
    if order_side is OrderSide.BUY:
        return cash - notional - fill.fee
    return cash + notional - fill.fee


async def _manage_position(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
    marks: Mapping[str, Decimal] | None = None,
    live_base_available: Decimal | None = None,
    risk_policy: RiskPolicyDefinition | None = None,
    portfolio: Sequence[DeploymentSnapshot] = (),
    htf_candles: Sequence[Candle] = (),
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None = None,
    reference_candles: Mapping[str, Sequence[Candle]] | None = None,
    fee_profile: FeeProfile | None = None,
) -> DeploymentSnapshot:
    """Exit on stop, take-profit, signal, or time, and expire working entry remainders."""
    if _active_entry(snapshot) is not None:
        snapshot = await _manage_working_entry(
            snapshot,
            strategy=strategy,
            candles=candles,
            candle=candle,
            product=product,
            broker=broker,
            store=store,
            marks=marks,
            live_base_available=live_base_available,
            risk_policy=risk_policy,
            portfolio=portfolio,
            fee_profile=fee_profile,
        )
        if snapshot.deployment.status is DeploymentStatus.STOPPED:
            return snapshot
        if snapshot.position is None and snapshot.deployment.phase is RuntimePhase.FLAT:
            return snapshot
    checked = await _fail_closed_on_split_state(snapshot, store=store)
    if checked is not None:
        return checked
    return await _manage_open_position(
        snapshot,
        strategy=strategy,
        candles=candles,
        candle=candle,
        product=product,
        broker=broker,
        store=store,
        htf_candles=htf_candles,
        indicator_timeframe_candles=indicator_timeframe_candles,
        reference_candles=reference_candles,
    )


def split_pending_entry(snapshot: DeploymentSnapshot) -> bool:
    """Whether a pending-entry book has neither a working entry nor a position.

    A RUNNING or PAUSED book in PENDING_ENTRY phase always holds its working
    entry until cancel/flatten. Missing both means the entry lifecycle was
    skipped or fill economics never committed (ADR 0057 violation) — the
    deployment 01a0bb90 stuck-pending_entry condition.
    """
    deployment = snapshot.deployment
    if deployment.phase is not RuntimePhase.PENDING_ENTRY:
        return False
    if deployment.status not in {DeploymentStatus.RUNNING, DeploymentStatus.PAUSED}:
        return False
    if deployment.mismatch_detail:
        return False
    return snapshot.position is None and _active_entry(snapshot) is None


def _split_state_detail(snapshot: DeploymentSnapshot) -> str:
    """Return the operator-facing reason a pending-entry book failed closed."""
    if any(
        order.status is OrderStatus.FILLED and not is_venue_protection(order.kind)
        for order in snapshot.orders
    ):
        return (
            "Filled entry order has no applied fill and no position; fill economics did not commit."
        )
    return (
        "Book is pending entry with no working entry order and no position; "
        "entry lifecycle did not advance."
    )


async def _fail_closed_on_split_state(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
) -> DeploymentSnapshot | None:
    """Pause split pending-entry state, or return None to continue the bar."""
    if not split_pending_entry(snapshot):
        return None
    return await _pause(
        snapshot,
        store=store,
        detail=_split_state_detail(snapshot),
    )


async def _manage_open_position(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
    htf_candles: Sequence[Candle] = (),
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None = None,
    reference_candles: Mapping[str, Sequence[Candle]] | None = None,
) -> DeploymentSnapshot:
    """Trail and protect an open book after pending-entry handling.

    Live books first learn any venue-attached TP/SL child of the filled entry, so a time,
    signal, or stop exit in this same cycle cancels that child before selling the base it
    holds. After the fill bar the ``exits.signal_exit`` rule is evaluated on every closed
    bar (ADR 0093). The paper stop still wins a same-bar tie (protective stop first), and
    a match marks the position so every later cycle keeps exiting until the book is flat.
    A book that is exiting on a signal is not trailed.
    """
    deployment = snapshot.deployment
    position = snapshot.position
    if deployment.phase not in _IN_MARKET or position is None:
        return snapshot
    if deployment.mode is DeploymentMode.LIVE:
        snapshot = await _adopt_venue_attached_child(
            snapshot, broker=broker, store=store, product_id=product.product_id
        )
        deployment = snapshot.deployment
        if snapshot.position is None:
            return snapshot
    if position.entered_bar != candle.starts_at:
        bars_held = deployment.bars_held + 1
        updated = with_runtime(deployment, updated_at=utc_now(), bars_held=bars_held)
        await store.save_deployment(updated)
        snapshot = await store.get_deployment(deployment.id)
    if snapshot.position is None:
        return snapshot
    return await _exit_trail_and_protect(
        snapshot,
        strategy=strategy,
        candles=candles,
        candle=candle,
        product=product,
        broker=broker,
        store=store,
        htf_candles=htf_candles,
        indicator_timeframe_candles=indicator_timeframe_candles,
        reference_candles=reference_candles,
    )


async def _exit_trail_and_protect(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
    htf_candles: Sequence[Candle],
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None,
    reference_candles: Mapping[str, Sequence[Candle]] | None,
) -> DeploymentSnapshot:
    """Evaluate the exit rule, apply the paper stop first, then mark or trail, and protect.

    Paper follows the backtest's same-bar precedence (ADR 0083, ADR 0093): the protective
    stop, then a touched take-profit (matched before this runs), then the signal exit, then
    the time exit. The stop is checked against the pre-trail level even when the time exit
    is also due on this bar, so a bar that trades through the stop never exits at its close.
    """
    live = snapshot.deployment.mode is DeploymentMode.LIVE
    snapshot, signal_matched = await _evaluate_signal_exit(
        snapshot,
        strategy=strategy,
        candles=candles,
        candle=candle,
        store=store,
        htf_candles=htf_candles,
        indicator_timeframe_candles=indicator_timeframe_candles,
        reference_candles=reference_candles,
    )
    position = snapshot.position
    if position is None:
        return snapshot
    if not live:
        stopped = await _paper_stop_exit_if_hit(
            snapshot,
            strategy=strategy,
            candle=candle,
            product=product,
            broker=broker,
            store=store,
            position=position,
        )
        if stopped is not None:
            return stopped
    if signal_matched:
        snapshot = await _mark_signal_exit(snapshot, candle=candle, store=store)
    elif position.signal_exit_bar is None:
        snapshot = await _apply_trailing(
            snapshot,
            strategy=strategy,
            candles=candles,
            candle=candle,
            product=product,
            store=store,
        )
    return await _protect_open_position(
        snapshot,
        strategy=strategy,
        candle=candle,
        product=product,
        broker=broker,
        store=store,
        skip_paper_stop=not live,
    )


async def _evaluate_signal_exit(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    candle: Candle,
    store: ExecutionStore,
    htf_candles: Sequence[Candle],
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None,
    reference_candles: Mapping[str, Sequence[Candle]] | None,
) -> tuple[DeploymentSnapshot, bool]:
    """Evaluate ``exits.signal_exit`` on this closed bar; True when it matched (ADR 0093).

    Not evaluated on the fill bar, for a book already exiting on a signal, or over a
    gapped window (indicator values across a gap would be wrong). A failed evaluation is
    journaled and pauses a running book, like a failed entry evaluation; the protective
    stop keeps guarding the position either way.
    """
    position = snapshot.position
    if position is None or signal_exit_condition(strategy.exits) is None:
        return snapshot, False
    if position.signal_exit_bar is not None or position.entered_bar >= candle.starts_at:
        return snapshot, False
    if not _window_contiguous(candles, strategy.timeframe):
        return snapshot, False
    try:
        evaluation = evaluate_latest_signal_exit(
            strategy,
            candles,
            htf_candles,
            indicator_timeframe_candles,
            reference_candles=reference_candles,
        )
    except SignalEvaluationError as error:
        note_evaluation_error(str(error))
        if snapshot.deployment.status is DeploymentStatus.RUNNING:
            return await _pause(snapshot, store=store, detail=str(error)), False
        return snapshot, False
    if evaluation is None:
        return snapshot, False
    note_exit_evaluation(evaluation)
    return snapshot, evaluation.outcome is EntryConditionOutcome.MATCHED


def _window_contiguous(candles: Sequence[Candle], timeframe: str) -> bool:
    """Whether the closed-bar window is gap-free on the strategy's decision clock."""
    step = parse_candle_interval(timeframe).duration
    return all(later.starts_at - earlier.starts_at == step for earlier, later in pairwise(candles))


async def _mark_signal_exit(
    snapshot: DeploymentSnapshot, *, candle: Candle, store: ExecutionStore
) -> DeploymentSnapshot:
    """Persist that this book is exiting on its signal before any order is touched.

    The marker lives on the position row, so it survives restarts and disappears with
    the position: every later cycle (``_due_exit_purpose``) keeps exiting instead of
    re-resting protection after a venue cancel completes between bars.
    """
    position = snapshot.position
    if position is None:
        return snapshot
    await store.save_position(
        replace(position, signal_exit_bar=candle.starts_at, updated_at=utc_now()),
        deployment_id=snapshot.deployment.id,
    )
    return await store.get_deployment(snapshot.deployment.id)


async def _paper_stop_exit_if_hit(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
    position: Position,
) -> DeploymentSnapshot | None:
    """Exit on the pre-trail stop when a closed bar trades through it."""
    if not paper_stop_hit(side=position.side, candle=candle, stop_price=position.stop_price):
        return None
    price = paper_stop_fill_price(side=position.side, candle=candle, stop_price=position.stop_price)
    return await _marketable_exit(
        snapshot,
        strategy=strategy,
        candle=candle,
        product=product,
        broker=broker,
        store=store,
        purpose=IntentPurpose.STOP,
        price=price,
    )


async def _apply_trailing(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    candle: Candle,
    product: MarketProduct,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Ratchet an ATR trailing stop after the fill bar; no-op when disabled."""
    position = snapshot.position
    policy = atr_trailing_stop(strategy.exits)
    if position is None or policy is None:
        return snapshot
    atr = named_atr(strategy, candles, policy.atr_indicator)
    if position.side is PositionSide.SHORT:
        state = ratcheted_short_stop(
            current_stop=position.stop_price,
            trail_extreme=position.trail_extreme,
            bar_low=candle.low,
            atr=atr,
            multiple=Decimal(policy.multiple),
            price_increment=product.price_increment,
            ratchet=position.entered_bar < candle.starts_at,
        )
    else:
        state = ratcheted_long_stop(
            current_stop=position.stop_price,
            trail_extreme=position.trail_extreme,
            bar_high=candle.high,
            atr=atr,
            multiple=Decimal(policy.multiple),
            price_increment=product.price_increment,
            ratchet=position.entered_bar < candle.starts_at,
        )
    if state.stop_price == position.stop_price and state.trail_extreme == position.trail_extreme:
        return snapshot
    await store.save_position(
        replace(
            position,
            stop_price=state.stop_price,
            trail_extreme=state.trail_extreme,
            updated_at=utc_now(),
        ),
        deployment_id=snapshot.deployment.id,
    )
    return await store.get_deployment(snapshot.deployment.id)


async def _protect_open_position(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
    skip_paper_stop: bool = False,
) -> DeploymentSnapshot:
    """Apply paper synthetic stops or live venue brackets after trailing.

    A pending flatten, a matched signal exit (ADR 0093), or a due time exit exits
    marketably at this bar's close (cancelling protection first) and never rests new
    protection. The signal exit precedes the time exit when both are due.
    """
    position = snapshot.position
    if position is None:
        return snapshot
    if flatten_requested(snapshot):
        return await _marketable_exit(
            snapshot,
            strategy=strategy,
            candle=candle,
            product=product,
            broker=broker,
            store=store,
            purpose=IntentPurpose.STOP,
            price=candle.close,
        )
    if position.signal_exit_bar is not None:
        return await _marketable_exit(
            snapshot,
            strategy=strategy,
            candle=candle,
            product=product,
            broker=broker,
            store=store,
            purpose=IntentPurpose.SIGNAL_EXIT,
            price=candle.close,
        )
    bars_held = snapshot.deployment.bars_held
    timed_out = bars_held >= strategy.exits.time_exit.max_bars_held
    live = snapshot.deployment.mode is DeploymentMode.LIVE
    if live and not timed_out:
        return await _ensure_live_bracket(
            snapshot, candle=candle, product=product, broker=broker, store=store
        )
    paper_stop = False
    if not skip_paper_stop:
        paper_stop = paper_stop_hit(
            side=position.side, candle=candle, stop_price=position.stop_price
        )
    if (not live and paper_stop) or timed_out:
        purpose = IntentPurpose.TIME_EXIT if timed_out else IntentPurpose.STOP
        price = (
            candle.close
            if timed_out
            else paper_stop_fill_price(
                side=position.side, candle=candle, stop_price=position.stop_price
            )
        )
        return await _marketable_exit(
            snapshot,
            strategy=strategy,
            candle=candle,
            product=product,
            broker=broker,
            store=store,
            purpose=purpose,
            price=price,
        )
    return await _ensure_take_profit(
        snapshot, candle=candle, product=product, broker=broker, store=store
    )


async def _adopt_venue_attached_child(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
    product_id: str,
) -> DeploymentSnapshot:
    """Learn an attached TP/SL child the venue reports for an already-filled entry.

    Coinbase may report ``attached_order_id`` only on GET order. Without it the open
    position looks unprotected and a second bracket would be rejected because the base
    is already on hold by the attached child. Ask the venue once before resting anything.
    """
    position = snapshot.position
    if position is None:
        return snapshot
    entry = filled_attached_entry(snapshot, position)
    if entry is None or entry.attached_child_venue_order_id or not entry.venue_order_id:
        return snapshot
    try:
        observed = await broker.get_order(
            venue_order_id=entry.venue_order_id, client_order_id=entry.client_order_id
        )
    except BrokerError:
        return snapshot
    child_id = observed.attached_child_venue_order_id
    if not child_id:
        return snapshot
    await store.save_order(
        replace(entry, attached_child_venue_order_id=child_id, updated_at=utc_now())
    )
    refreshed = await store.get_deployment(snapshot.deployment.id)
    return await import_attached_children(
        refreshed, broker=broker, store=store, product_id=product_id
    )


async def _ensure_live_bracket(
    snapshot: DeploymentSnapshot,
    *,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Rest one venue OCO bracket, replacing it when the working stop ratchets.

    Never rests new protection while a flatten is pending or a filled exit still awaits
    its fills, waits (without pausing) on an accepted cancel of the bracket it replaces,
    and holds back an identical bracket the venue already rejected (see exit_guards).
    """
    snapshot = await _adopt_venue_attached_child(
        snapshot, broker=broker, store=store, product_id=product.product_id
    )
    position = snapshot.position
    if position is None:
        return snapshot
    if _attached_entry_covers(snapshot, position):
        return await _mark_pending_exit(snapshot, store=store)
    cover = exit_order_side(position.side)
    existing = _active_side(snapshot.orders, cover)
    if existing is not None and _bracket_matches(existing, position):
        return await _mark_pending_exit(snapshot, store=store)
    if flatten_requested(snapshot) or exit_fill_pending(snapshot) is not None:
        return snapshot
    if existing is not None:
        snapshot = await _cancel_one_order(existing, broker=broker, store=store)
        remaining = _active_side(snapshot.orders, cover)
        if remaining is not None:
            if cancel_pending(remaining):
                return snapshot
            return await _pause(snapshot, store=store, detail=BRACKET_REPLACE_CANCEL_DETAIL)
    return await _submit_live_bracket(
        snapshot,
        position=position,
        candle=candle,
        product=product,
        broker=broker,
        store=store,
    )


def _bracket_matches(order: Order, position: Position) -> bool:
    """Whether one resting order is the venue protection covering exactly this position.

    A book with a take-profit needs the TP/SL OCO; a book without one (ADR 0090) needs a
    stop-limit triggered at the working stop.
    """
    if order.stop_trigger_price != position.stop_price or order.quantity != position.quantity:
        return False
    if position.target_price is None:
        return order.kind is OrderKind.STOP_LIMIT
    return order.kind is OrderKind.TRIGGER_BRACKET and order.price == position.target_price


async def _submit_live_bracket(
    snapshot: DeploymentSnapshot,
    *,
    position: Position,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Submit one live protective order unless an identical rejection is still latched.

    With a take-profit this is the ``trigger_bracket_gtc`` OCO (limit = target). Without
    one (ADR 0090) it is a ``stop_limit_stop_limit_gtc`` triggered at the stop whose
    limit sits 5% through it, the same offset Coinbase applies to a bracket's stop leg.
    """
    rejection = bracket_rejection(snapshot, position)
    if rejection is not None and rejection_latched(snapshot, rejection, now=utc_now()):
        return await _pause_bracket_rejected(snapshot, store=store, position=position)
    cover = exit_order_side(position.side)
    target = position.target_price
    kind = OrderKind.TRIGGER_BRACKET
    price = target
    if target is None:
        kind = OrderKind.STOP_LIMIT
        price = protective_stop_limit_price(
            cover_side=cover,
            stop_price=position.stop_price,
            price_increment=product.price_increment,
        )
        if price <= 0:
            return await _pause(snapshot, store=store, detail=STOP_LIMIT_PRICE_DETAIL)
    order = await submit_intent(
        store=store,
        broker=broker,
        deployment_id=snapshot.deployment.id,
        product_id=product.product_id,
        purpose=IntentPurpose.BRACKET,
        side=cover,
        kind=kind,
        quantity=position.quantity,
        price=price,
        stop_trigger_price=position.stop_price,
        candle=candle,
    )
    current = await store.get_deployment(snapshot.deployment.id)
    if order.status is OrderStatus.OPEN:
        return await _mark_pending_exit(current, store=store)
    if order.status in {OrderStatus.UNKNOWN, OrderStatus.PENDING}:
        return await _pause(current, store=store, detail=BRACKET_UNCONFIRMED_DETAIL)
    if order.status is OrderStatus.REJECTED:
        return await _pause_bracket_rejected(current, store=store, position=position)
    return await _pause(current, store=store, detail=BRACKET_NOT_RESTED_DETAIL)


async def _pause_bracket_rejected(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    position: Position,
) -> DeploymentSnapshot:
    """Pause once per venue rejection of a live bracket and audit it."""
    rejection = bracket_rejection(snapshot, position)
    if rejection is None:
        return await _pause(snapshot, store=store, detail=BRACKET_NOT_RESTED_DETAIL)
    return await _pause_rejected(snapshot, store=store, rejection=rejection, position=position)


async def _pause_rejected(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    rejection: SubmitRejection,
    position: Position,
) -> DeploymentSnapshot:
    """Pause once per deterministic protective rejection and audit it; latched cycles no-op."""
    detail = rejection_detail(rejection)
    deployment = snapshot.deployment
    if deployment.mismatch_detail == detail and deployment.status is not DeploymentStatus.RUNNING:
        return snapshot
    paused = await _pause(snapshot, store=store, detail=detail)
    await record_execution_audit(
        action="protective_submit_latched",
        outcome=AuditEventOutcome.FAILURE,
        detail=(
            f"deployment_id={deployment.id} rejections={rejection.count} "
            f"retry_after={rejection.retry_at().isoformat()} reason={rejection.reason[:300]}: "
            f"identical {rejection.label} re-submits are held back with exponential backoff."
        ),
        product_id=position.product_id or deployment.product_id,
    )
    return paused


async def _mark_pending_exit(
    snapshot: DeploymentSnapshot, *, store: ExecutionStore
) -> DeploymentSnapshot:
    """Record that an exit order is working without changing cash or inventory."""
    pending = with_runtime(
        snapshot.deployment, updated_at=utc_now(), phase=RuntimePhase.PENDING_EXIT
    )
    await store.save_deployment(pending)
    return await store.get_deployment(snapshot.deployment.id)


async def _manage_working_entry(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
    marks: Mapping[str, Decimal] | None,
    live_base_available: Decimal | None,
    risk_policy: RiskPolicyDefinition | None,
    portfolio: Sequence[DeploymentSnapshot],
    fee_profile: FeeProfile | None = None,
) -> DeploymentSnapshot:
    """Wait, cancel, or reprice a working entry remainder in any phase."""
    deployment = snapshot.deployment
    waited = deployment.pending_entry_bars + 1
    open_entry = _active_entry(snapshot)
    has_position = snapshot.position is not None
    if open_entry is None:
        if has_position:
            cleared = with_runtime(deployment, updated_at=utc_now(), pending_entry_bars=0)
            await store.save_deployment(cleared)
            return await store.get_deployment(deployment.id)
        return await _flatten_pending(snapshot, store=store, cooldown_bars=1)
    if open_entry.status is not OrderStatus.OPEN or open_entry.venue_order_id is None:
        return await _pause(
            snapshot,
            store=store,
            detail="Entry order is unconfirmed; reconcile before retrying.",
        )
    if waited < strategy.execution.max_entry_wait_bars:
        waited_state = with_runtime(deployment, updated_at=utc_now(), pending_entry_bars=waited)
        await store.save_deployment(waited_state)
        return await store.get_deployment(deployment.id)
    if strategy.execution.on_unfilled_entry == "reprice" and not can_reprice_risk_up(deployment):
        return snapshot
    snapshot = await _cancel_one_order(open_entry, broker=broker, store=store)
    remaining = _active_entry(snapshot)
    if remaining is not None and remaining.status is not OrderStatus.CANCELED:
        return await _pause(
            snapshot,
            store=store,
            detail="Unfilled entry could not be canceled before retrying.",
        )
    if strategy.execution.on_unfilled_entry == "reprice" and can_reprice_risk_up(
        snapshot.deployment
    ):
        phase = RuntimePhase.OPEN if has_position else RuntimePhase.PENDING_ENTRY
        return await _reprice_entry(
            snapshot,
            strategy=strategy,
            candles=candles,
            candle=candle,
            product=product,
            broker=broker,
            store=store,
            prior=open_entry,
            marks=marks,
            live_base_available=live_base_available,
            phase=phase,
            risk_policy=risk_policy,
            portfolio=portfolio,
            fee_profile=fee_profile,
        )
    if has_position:
        abandoned = with_runtime(snapshot.deployment, updated_at=utc_now(), pending_entry_bars=0)
        await store.save_deployment(abandoned)
        return await store.get_deployment(snapshot.deployment.id)
    return await _flatten_pending(
        snapshot, store=store, cooldown_bars=max(strategy.entry.cooldown_bars, 1)
    )


async def _flatten_pending(
    snapshot: DeploymentSnapshot, *, store: ExecutionStore, cooldown_bars: int
) -> DeploymentSnapshot:
    """Return a flat deployment after an unfilled entry is abandoned."""
    flattened = with_runtime(
        snapshot.deployment,
        updated_at=utc_now(),
        phase=RuntimePhase.FLAT,
        pending_entry_bars=0,
        cooldown_bars_remaining=cooldown_bars,
        clear_pending_levels=True,
    )
    await store.save_deployment(flattened)
    return await store.get_deployment(snapshot.deployment.id)


async def _reprice_entry(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
    prior: Order,
    marks: Mapping[str, Decimal] | None = None,
    live_base_available: Decimal | None = None,
    phase: RuntimePhase = RuntimePhase.PENDING_ENTRY,
    risk_policy: RiskPolicyDefinition | None = None,
    portfolio: Sequence[DeploymentSnapshot] = (),
    fee_profile: FeeProfile | None = None,
) -> DeploymentSnapshot:
    """Submit a replacement post-only entry at remaining qty after sizing and risk re-admission."""
    remaining_qty = remaining_quantity(prior)
    if remaining_qty <= 0 or not can_reprice_risk_up(snapshot.deployment):
        return snapshot
    is_pyramid = phase is RuntimePhase.OPEN or snapshot.position is not None
    try:
        entry_price = await _await_maker_limit(
            broker, product_id=product.product_id, mark=candle.close, side=prior.side
        )
    except BrokerError:
        if is_pyramid:
            abandoned = with_runtime(
                snapshot.deployment, updated_at=utc_now(), pending_entry_bars=0
            )
            await store.save_deployment(abandoned)
            return await store.get_deployment(snapshot.deployment.id)
        return await _pause(
            snapshot,
            store=store,
            detail="Maker entry price is unavailable for repricing.",
            phase=RuntimePhase.FLAT,
        )
    atr = latest_atr(strategy, candles)
    if atr is None:
        return snapshot
    side = PositionSide(strategy.entry.side)
    sized = _size_entry_or_add(
        snapshot,
        strategy=strategy,
        product=product,
        entry_price=entry_price,
        atr=atr,
        side=side,
        is_pyramid_add=is_pyramid,
        # Preserve legacy reprice sizing when the strategy has not enabled the new guard.
        fee_profile=fee_profile if strategy.entry.economic_guard is not None else None,
    )
    if isinstance(sized, EntrySkipReason):
        note_entry_skip(sized)
        return snapshot
    stop_price, target_price = _legal_reprice_geometry(
        side=side,
        entry_price=entry_price,
        stop_price=sized.stop_price,
        target_price=sized.target_price,
    )
    policy = risk_policy or compiled_default_risk_policy()
    admitted = _entry_verdict(
        snapshot,
        product_id=product.product_id,
        notional=entry_price * remaining_qty,
        risk_policy=policy,
        portfolio=portfolio,
        observation=_bar_observation(
            product_id=product.product_id,
            candle=candle,
            proposed_price=entry_price,
            marks=marks,
        ),
        is_pyramid_add=is_pyramid,
    )
    if admitted.decision is RiskDecision.DENY:
        if pauses_risk_increasing(admitted.reason_code):
            return await _pause_for_breaker(
                snapshot, store=store, portfolio=portfolio, verdict=admitted
            )
        return snapshot
    if (
        side is PositionSide.SHORT
        and snapshot.deployment.mode is DeploymentMode.LIVE
        and (live_base_available is None or live_base_available < remaining_qty)
    ):
        return await _pause(
            snapshot,
            store=store,
            detail=(
                "INSUFFICIENT_BASE_FOR_SPOT_SHORT: Coinbase spot shorts require available base."
            ),
        )
    return await _submit_repriced_entry(
        snapshot,
        broker=broker,
        store=store,
        product=product,
        candle=candle,
        prior=prior,
        remaining_qty=remaining_qty,
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
        is_pyramid=is_pyramid,
        phase=phase,
        attach=(
            not is_pyramid
            and target_price is not None
            and atr_trailing_stop(strategy.exits) is None
        ),
    )


async def _submit_repriced_entry(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
    product: MarketProduct,
    candle: Candle,
    prior: Order,
    remaining_qty: Decimal,
    entry_price: Decimal,
    stop_price: Decimal,
    target_price: Decimal | None,
    is_pyramid: bool,
    phase: RuntimePhase,
    attach: bool,
) -> DeploymentSnapshot:
    """Persist pending levels and rest the replacement maker order."""
    reset = with_runtime(snapshot.deployment, updated_at=utc_now(), pending_entry_bars=0)
    if not is_pyramid:
        reset = _with_pending_levels(reset, stop_price=stop_price, target_price=target_price)
    await store.save_deployment(reset)
    order = await submit_intent(
        store=store,
        broker=broker,
        deployment_id=snapshot.deployment.id,
        product_id=product.product_id,
        purpose=IntentPurpose.ENTRY,
        side=prior.side,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=remaining_qty,
        price=entry_price,
        candle=candle,
        stop_trigger_price=stop_price if attach else None,
        take_profit_price=target_price if attach else None,
        pyramid_add=is_pyramid,
        idempotency_key=_signal_intent_key(
            snapshot.deployment.id, IntentPurpose.ENTRY, candle.starts_at, product.product_id
        ),
    )
    if order.status is OrderStatus.UNKNOWN:
        return await _pause(
            await store.get_deployment(snapshot.deployment.id),
            store=store,
            detail="Repriced entry submit is unconfirmed.",
        )
    pending = with_runtime(reset, updated_at=utc_now(), phase=phase, pending_entry_bars=0)
    await store.save_deployment(pending)
    return await store.get_deployment(snapshot.deployment.id)


def _legal_reprice_geometry(
    *,
    side: PositionSide,
    entry_price: Decimal,
    stop_price: Decimal,
    target_price: Decimal | None,
) -> tuple[Decimal, Decimal | None]:
    """Preserve remaining qty's legal stop/target; drop an obsolete target below a new buy.

    A strategy without a take-profit keeps ``None``: there is no target to repair.
    """
    if target_price is None:
        return stop_price, None
    if side is PositionSide.LONG and target_price <= entry_price:
        width = entry_price - stop_price
        if width <= 0:
            width = entry_price * Decimal("0.01")
        return stop_price, entry_price + width
    if side is PositionSide.SHORT and target_price >= entry_price:
        width = stop_price - entry_price
        if width <= 0:
            width = entry_price * Decimal("0.01")
        return stop_price, entry_price - width
    return stop_price, target_price


async def _marketable_exit(
    snapshot: DeploymentSnapshot,
    *,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
    purpose: IntentPurpose,
    price: Decimal,
    strategy: StrategyDefinition | None = None,
    cooldown_bars: int | None = None,
) -> DeploymentSnapshot:
    """Cancel resting exits, then submit a marketable cover of the open position.

    Live books first adopt the entry's venue-attached child so it is cancelled too (it
    holds the base the exit sells). An accepted-but-unfinished cancel waits for the next
    cycle instead of pausing; a filled exit whose fills lag is settled, never re-sent.
    """
    if snapshot.position is None:
        return snapshot
    if snapshot.deployment.mode is DeploymentMode.LIVE:
        snapshot = await _reconcile_stopped_live(snapshot, broker=broker, store=store)
        snapshot = await _adopt_venue_attached_child(
            snapshot, broker=broker, store=store, product_id=product.product_id
        )
    filled_exit = exit_fill_pending(snapshot)
    if filled_exit is not None:
        return await _apply_immediate_exit_fill(
            snapshot,
            order=filled_exit,
            broker=broker,
            store=store,
            product_id=product.product_id,
            cooldown_bars=_exit_cooldown(strategy, cooldown_bars),
        )
    if _unsettled_fill_evidence(snapshot):
        return await _pause(snapshot, store=store, detail=FILLED_WITHOUT_REST_FILLS_DETAIL)
    await _cancel_open_orders(snapshot, broker=broker, store=store)
    snapshot = await store.get_deployment(snapshot.deployment.id)
    if active_orders(snapshot) and all(cancel_pending(order) for order in active_orders(snapshot)):
        return await _mark_pending_exit(snapshot, store=store)
    snapshot = await _reconcile_stopped_live(snapshot, broker=broker, store=store)
    position = snapshot.position
    if position is None:
        return snapshot
    if _unsettled_fill_evidence(snapshot):
        return await _pause(snapshot, store=store, detail=FILLED_WITHOUT_REST_FILLS_DETAIL)
    blocking = active_orders(snapshot)
    if blocking:
        if all(cancel_pending(order) for order in blocking):
            return await _mark_pending_exit(snapshot, store=store)
        return await _pause(snapshot, store=store, detail=CANCEL_BEFORE_EXIT_DETAIL)
    return await _submit_marketable_exit(
        snapshot,
        position=position,
        cooldown_bars=_exit_cooldown(strategy, cooldown_bars),
        candle=candle,
        product=product,
        broker=broker,
        store=store,
        purpose=purpose,
        price=price,
    )


async def _submit_marketable_exit(
    snapshot: DeploymentSnapshot,
    *,
    position: Position,
    cooldown_bars: int,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
    purpose: IntentPurpose,
    price: Decimal,
) -> DeploymentSnapshot:
    """Send one marketable cover unless an identical live rejection is still latched."""
    live = snapshot.deployment.mode is DeploymentMode.LIVE
    rejection = exit_rejection(snapshot, position) if live else None
    if rejection is not None and rejection_latched(snapshot, rejection, now=utc_now()):
        return await _pause_rejected(snapshot, store=store, rejection=rejection, position=position)
    order = await submit_intent(
        store=store,
        broker=broker,
        deployment_id=snapshot.deployment.id,
        product_id=product.product_id,
        purpose=purpose,
        side=exit_order_side(position.side),
        kind=OrderKind.MARKETABLE,
        quantity=position.quantity,
        price=price,
        candle=candle,
    )
    if order.status is OrderStatus.FILLED:
        return await _apply_immediate_exit_fill(
            snapshot,
            order=order,
            broker=broker,
            store=store,
            product_id=product.product_id,
            cooldown_bars=cooldown_bars,
        )
    current = await store.get_deployment(snapshot.deployment.id)
    rejection = exit_rejection(current, position) if live else None
    if order.status is OrderStatus.REJECTED and rejection is not None:
        return await _pause_rejected(current, store=store, rejection=rejection, position=position)
    return await _pause(current, store=store, detail=MARKETABLE_EXIT_UNCONFIRMED_DETAIL)


async def _apply_immediate_exit_fill(
    snapshot: DeploymentSnapshot,
    *,
    order: Order,
    broker: Broker,
    store: ExecutionStore,
    product_id: str,
    cooldown_bars: int,
) -> DeploymentSnapshot:
    """Apply a marketable cover fill that the broker reported as already complete.

    Paper records the fill at submit. Live fills arrive through REST, so they are listed
    for this order now; when Coinbase has not published them yet the book stays
    PENDING_EXIT and the next cycle retries, without resting protection or re-exiting.
    """
    current = await store.get_deployment(snapshot.deployment.id)
    fill = next(
        (
            item
            for item in current.fills
            if item.order_id == order.id and item.economics_applied_at is None
        ),
        None,
    )
    if fill is not None:
        return await apply_fill(
            current, fill=fill, order=order, store=store, cooldown_bars=cooldown_bars
        )
    if current.deployment.mode is not DeploymentMode.LIVE:
        return current
    ingested = await ingest_order_fills(
        current,
        order=order,
        broker=broker,
        store=store,
        product_id=product_id,
        cooldown_bars=cooldown_bars,
    )
    if exit_fill_pending(ingested) is not None:
        return await _mark_pending_exit(ingested, store=store)
    return ingested


async def _ensure_exit_protection(
    snapshot: DeploymentSnapshot,
    *,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
    strategy: StrategyDefinition | None = None,
) -> DeploymentSnapshot:
    """Rest paper take-profit or a live venue bracket when already evaluated.

    With the strategy known, a book whose exit is already due (pending flatten, or a
    reached time exit still waiting on a venue cancel) keeps exiting instead of
    re-resting protection between bars.
    """
    purpose = _due_exit_purpose(snapshot, strategy)
    if strategy is not None and purpose is not None:
        return await _marketable_exit(
            snapshot,
            strategy=strategy,
            candle=candle,
            product=product,
            broker=broker,
            store=store,
            purpose=purpose,
            price=candle.close,
        )
    if snapshot.deployment.mode is DeploymentMode.LIVE:
        return await _ensure_live_bracket(
            snapshot, candle=candle, product=product, broker=broker, store=store
        )
    return await _ensure_take_profit(
        snapshot, candle=candle, product=product, broker=broker, store=store
    )


def _due_exit_purpose(
    snapshot: DeploymentSnapshot, strategy: StrategyDefinition | None
) -> IntentPurpose | None:
    """Return why a live open book must keep exiting between bars, or None to protect it.

    A pending flatten exits as STOP; a position marked by a matched ``exits.signal_exit``
    rule keeps exiting as SIGNAL_EXIT (ADR 0093); a reached ``max_bars_held`` keeps
    exiting as TIME_EXIT, so a cancel that completes between bars is followed by the exit
    rather than by a freshly rested bracket. Paper exits only on closed bars.
    """
    if snapshot.deployment.mode is not DeploymentMode.LIVE or snapshot.position is None:
        return None
    if flatten_requested(snapshot):
        return IntentPurpose.STOP
    if snapshot.position.signal_exit_bar is not None:
        return IntentPurpose.SIGNAL_EXIT
    if strategy is None:
        return None
    if snapshot.deployment.bars_held >= strategy.exits.time_exit.max_bars_held:
        return IntentPurpose.TIME_EXIT
    return None


async def _ensure_take_profit(
    snapshot: DeploymentSnapshot,
    *,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Rest a post-only take-profit when the position has none.

    A book without a target (``take_profit: {"kind": "none"}``) rests nothing: paper
    enforces its stop synthetically on closed bars and exits on the trail or time exit.
    """
    position = snapshot.position
    if position is None or position.target_price is None:
        return snapshot
    cover = exit_order_side(position.side)
    if _active_side(snapshot.orders, cover) is not None:
        pending = with_runtime(
            snapshot.deployment, updated_at=utc_now(), phase=RuntimePhase.PENDING_EXIT
        )
        await store.save_deployment(pending)
        return await store.get_deployment(snapshot.deployment.id)
    order = await submit_intent(
        store=store,
        broker=broker,
        deployment_id=snapshot.deployment.id,
        product_id=product.product_id,
        purpose=IntentPurpose.TAKE_PROFIT,
        side=cover,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=position.quantity,
        price=position.target_price,
        candle=candle,
    )
    if order.status is OrderStatus.OPEN:
        pending = with_runtime(
            snapshot.deployment, updated_at=utc_now(), phase=RuntimePhase.PENDING_EXIT
        )
        await store.save_deployment(pending)
        return await store.get_deployment(snapshot.deployment.id)
    if order.status in {OrderStatus.UNKNOWN, OrderStatus.PENDING}:
        return await _pause(
            await store.get_deployment(snapshot.deployment.id),
            store=store,
            detail="Take-profit submit is unconfirmed.",
        )
    return await store.get_deployment(snapshot.deployment.id)


def _entry_attempt_mode(
    snapshot: DeploymentSnapshot, strategy: StrategyDefinition
) -> Literal["new", "pyramid"] | None:
    """Return whether this bar may open a book, add to one, or should skip."""
    deployment = snapshot.deployment
    if not entries_allowed(deployment):
        return None
    if deployment.phase is RuntimePhase.FLAT:
        if deployment.cooldown_bars_remaining > 0:
            return None
        if (
            _document_open_book_count(snapshot)
            >= strategy.portfolio_limits.max_concurrent_positions
        ):
            return None
        return "new"
    if (
        deployment.phase is RuntimePhase.OPEN
        and snapshot.position is not None
        and _active_entry(snapshot) is None
    ):
        return "pyramid"
    return None


def _note_entry_gate(snapshot: DeploymentSnapshot) -> None:
    """Journal why no entry was attempted, only when a decision journal is observing."""
    if decision_observation_active():
        note_entry_gate(_entry_skip_reason(snapshot))


def _entry_skip_reason(snapshot: DeploymentSnapshot) -> DecisionSkipReason | None:
    """Name why ``_entry_attempt_mode`` skipped this bar; journal-only, never gates trading.

    Called only after ``_entry_attempt_mode`` returned None, so a FLAT book without
    cooldown was refused by the document's concurrent-position cap. None means the
    book is holding with protection working (nothing to name).
    """
    deployment = snapshot.deployment
    if not entries_allowed(deployment):
        return DecisionSkipReason.ENTRIES_DISABLED
    if deployment.phase is RuntimePhase.FLAT:
        if deployment.cooldown_bars_remaining > 0:
            return DecisionSkipReason.COOLDOWN
        return DecisionSkipReason.MAX_OPEN_POSITIONS
    if deployment.phase is RuntimePhase.PENDING_ENTRY or _active_entry(snapshot) is not None:
        return DecisionSkipReason.PENDING_ENTRY
    return None


async def _maybe_enter(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    product: MarketProduct,
    candles: Sequence[Candle],
    candle: Candle,
    broker: Broker,
    store: ExecutionStore,
    risk_policy: RiskPolicyDefinition,
    portfolio: Sequence[DeploymentSnapshot],
    htf_candles: Sequence[Candle] = (),
    indicator_timeframe_candles: Mapping[str, Sequence[Candle]] | None = None,
    reference_candles: Mapping[str, Sequence[Candle]] | None = None,
    live_base_available: Decimal | None = None,
    marks: Mapping[str, Decimal] | None = None,
    fee_profile: FeeProfile | None = None,
) -> DeploymentSnapshot:
    """Place a post-only entry when flat, or a same-side add when pyramiding allows it.

    Decision-journal notes only record facts; every branch decides exactly as before.
    """
    mode = _entry_attempt_mode(snapshot, strategy)
    if mode is None:
        _note_entry_gate(snapshot)
        return snapshot
    pyramid_add = mode == "pyramid"
    deployment = snapshot.deployment
    visible_htf = htf_candles
    htf_filter = strategy.htf_filter
    if htf_filter is not None:
        visible_htf = htf_bars_closed_at_or_before(
            htf_candles,
            close_at=ltf_close(candle.starts_at, strategy.timeframe),
            htf_timeframe=htf_filter.timeframe,
        )
    try:
        evaluation = evaluate_latest_entry_evidence(
            strategy,
            candles,
            visible_htf,
            indicator_timeframe_candles,
            reference_candles=reference_candles,
        )
    except SignalEvaluationError as error:
        note_evaluation_error(str(error))
        return await _pause(snapshot, store=store, detail=str(error))
    note_evaluation(evaluation)
    outcome = evaluation.outcome
    now = utc_now()
    signaled = with_runtime(
        deployment,
        updated_at=now,
        last_signal=outcome.value,
        last_signal_event_at=candle.starts_at,
        last_signal_processed_at=now,
    )
    await store.save_deployment(signaled)
    snapshot = await store.get_deployment(deployment.id)
    position = snapshot.position
    if outcome is not EntryConditionOutcome.MATCHED:
        return snapshot
    evaluated_at = candle.starts_at + parse_candle_interval(strategy.timeframe).duration
    if not signal_still_valid(
        candle=candle,
        timeframe=strategy.timeframe,
        now=evaluated_at,
        current_quote=candle.close,
    ):
        note_freshness(_STALE_SIGNAL_VERDICT)
        return snapshot
    fresh = entry_prerequisites(
        product=product,
        candle=candle,
        now=evaluated_at,
        timeframe=strategy.timeframe,
        venue_balance_known=(
            live_sizing_cash(snapshot.deployment) is not None
            or snapshot.deployment.mode is DeploymentMode.PAPER
        ),
    )
    if fresh.decision is RiskDecision.DENY:
        note_freshness(fresh)
        return snapshot
    if pyramid_add:
        if position is None:
            return snapshot
        if not can_pyramid_add(
            strategy=strategy,
            side=position.side.value,
            entry_price=position.entry_price,
            mark=candle.close,
            add_count=position.add_count,
        ):
            _note_pyramid_refusal(strategy)
            return snapshot
    return await _submit_sized_entry(
        snapshot,
        strategy=strategy,
        product=product,
        candles=candles,
        candle=candle,
        broker=broker,
        store=store,
        risk_policy=risk_policy,
        portfolio=portfolio,
        live_base_available=live_base_available,
        marks=marks,
        is_pyramid_add=pyramid_add,
        fee_profile=fee_profile,
    )


def _note_pyramid_refusal(strategy: StrategyDefinition) -> None:
    """Journal why a matched signal did not add to the open book."""
    if strategy.entry.pyramiding is None:
        note_entry_block("PYRAMID_DISABLED", "The strategy does not enable pyramiding.")
        return
    note_entry_block(
        "PYRAMID_NOT_ALLOWED",
        "Pyramiding rules refused this add (add limit reached or price not beyond entry).",
    )


def _size_entry_or_add(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    product: MarketProduct,
    entry_price: Decimal,
    atr: Decimal,
    side: PositionSide,
    is_pyramid_add: bool,
    fee_profile: FeeProfile | None = None,
) -> SizedEntry | EntrySkipReason:
    """Size a new book or a same-side add against remaining quote cash, or name the skip."""
    if (
        strategy.entry.economic_guard is not None
        and snapshot.deployment.mode is DeploymentMode.LIVE
        and fee_profile is None
    ):
        return EntrySkipReason.ECONOMICS_FEE_UNAVAILABLE
    fee_rate = _entry_fee_rate(snapshot.deployment, fee_profile=fee_profile)
    sizing_cash = live_sizing_cash(snapshot.deployment)
    if sizing_cash is None:
        return EntrySkipReason.SIZING_CASH_UNAVAILABLE
    if not is_pyramid_add:
        return size_entry_or_skip(
            strategy=strategy,
            cash=sizing_cash,
            entry_price=entry_price,
            atr=atr,
            product=product,
            fee_rate=fee_rate,
            side=side,
        )
    position = snapshot.position
    if position is None:
        return EntrySkipReason.NO_OPEN_POSITION
    return size_pyramid_add_or_skip(
        strategy=strategy,
        cash=sizing_cash,
        entry_price=entry_price,
        existing_stop=position.stop_price,
        existing_target=position.target_price,
        product=product,
        fee_rate=fee_rate,
        side=side,
    )


def _runtime_for_admitted_entry(
    deployment: Deployment,
    *,
    sized: SizedEntry,
    strategy: StrategyDefinition,
    is_pyramid_add: bool,
) -> tuple[Deployment, bool]:
    """Stamp pending-entry or keep OPEN, and decide whether live brackets attach.

    A Coinbase attached ``trigger_bracket_gtc`` needs a take-profit limit, so an entry
    without a target never attaches; its stop-only protection rests after the fill.
    """
    if is_pyramid_add:
        pending = with_runtime(
            deployment,
            updated_at=utc_now(),
            phase=RuntimePhase.OPEN,
            pending_entry_bars=0,
        )
        return pending, False
    pending = with_runtime(
        _with_pending_levels(
            deployment, stop_price=sized.stop_price, target_price=sized.target_price
        ),
        updated_at=utc_now(),
        phase=RuntimePhase.PENDING_ENTRY,
        pending_entry_bars=0,
    )
    attach = sized.target_price is not None and atr_trailing_stop(strategy.exits) is None
    return pending, attach


def _with_pending_levels(
    deployment: Deployment, *, stop_price: Decimal, target_price: Decimal | None
) -> Deployment:
    """Replace both pending levels; a None target (no take-profit) clears any stale one."""
    cleared = with_runtime(deployment, updated_at=utc_now(), clear_pending_levels=True)
    return with_runtime(
        cleared,
        updated_at=utc_now(),
        pending_stop_price=stop_price,
        pending_target_price=target_price,
    )


async def _submit_sized_entry(
    snapshot: DeploymentSnapshot,
    *,
    strategy: StrategyDefinition,
    product: MarketProduct,
    candles: Sequence[Candle],
    candle: Candle,
    broker: Broker,
    store: ExecutionStore,
    risk_policy: RiskPolicyDefinition,
    portfolio: Sequence[DeploymentSnapshot],
    live_base_available: Decimal | None = None,
    marks: Mapping[str, Decimal] | None = None,
    is_pyramid_add: bool = False,
    fee_profile: FeeProfile | None = None,
) -> DeploymentSnapshot:
    """Size an entry or same-side add and rest a post-only order when policy allows it."""
    atr = latest_atr(strategy, candles)
    if atr is None:
        note_entry_block("ATR_UNDEFINED", "The initial-stop ATR has no value on this bar.")
        return snapshot
    side = PositionSide(strategy.entry.side)
    open_side = entry_order_side(side)
    try:
        entry_price = await _await_maker_limit(
            broker, product_id=product.product_id, mark=candle.close, side=open_side
        )
    except BrokerError:
        note_entry_block("MAKER_PRICE_UNAVAILABLE", "Maker entry price is unavailable.")
        return await _pause(snapshot, store=store, detail="Maker entry price is unavailable.")
    sized = _size_entry_or_add(
        snapshot,
        strategy=strategy,
        product=product,
        entry_price=entry_price,
        atr=atr,
        side=side,
        is_pyramid_add=is_pyramid_add,
        fee_profile=fee_profile,
    )
    if isinstance(sized, EntrySkipReason):
        note_entry_skip(sized)
        return snapshot
    if (
        side is PositionSide.SHORT
        and snapshot.deployment.mode is DeploymentMode.LIVE
        and (live_base_available is None or live_base_available < sized.quantity)
    ):
        note_entry_block(
            "INSUFFICIENT_BASE_FOR_SPOT_SHORT", "Coinbase spot shorts require available base."
        )
        return await _pause(
            snapshot,
            store=store,
            detail=(
                "INSUFFICIENT_BASE_FOR_SPOT_SHORT: Coinbase spot shorts require available base."
            ),
        )
    admitted = _entry_verdict(
        snapshot,
        product_id=product.product_id,
        notional=sized.notional,
        risk_policy=risk_policy,
        portfolio=portfolio,
        observation=_bar_observation(
            product_id=product.product_id,
            candle=candle,
            proposed_price=sized.entry_price,
            marks=marks,
        ),
        is_pyramid_add=is_pyramid_add,
    )
    if admitted.decision is RiskDecision.DENY:
        if pauses_risk_increasing(admitted.reason_code):
            return await _pause_for_breaker(
                snapshot, store=store, portfolio=portfolio, verdict=admitted
            )
        return snapshot
    pending, attach = _runtime_for_admitted_entry(
        snapshot.deployment, sized=sized, strategy=strategy, is_pyramid_add=is_pyramid_add
    )
    await store.save_deployment(pending)
    order = await submit_intent(
        store=store,
        broker=broker,
        deployment_id=snapshot.deployment.id,
        product_id=product.product_id,
        purpose=IntentPurpose.ENTRY,
        side=open_side,
        kind=OrderKind.POST_ONLY_LIMIT,
        quantity=sized.quantity,
        price=sized.entry_price,
        candle=candle,
        stop_trigger_price=sized.stop_price if attach else None,
        take_profit_price=sized.target_price if attach else None,
        pyramid_add=is_pyramid_add,
        idempotency_key=_signal_intent_key(
            snapshot.deployment.id, IntentPurpose.ENTRY, candle.starts_at, product.product_id
        ),
    )
    snapshot = await store.get_deployment(snapshot.deployment.id)
    if order.status is OrderStatus.UNKNOWN:
        return await _pause(snapshot, store=store, detail="Entry submit is unconfirmed.")
    if order.status is OrderStatus.REJECTED:
        if is_pyramid_add:
            restored = with_runtime(
                snapshot.deployment,
                updated_at=utc_now(),
                phase=RuntimePhase.OPEN,
                pending_entry_bars=0,
            )
            await store.save_deployment(restored)
            return await store.get_deployment(snapshot.deployment.id)
        return await _flatten_pending(
            snapshot, store=store, cooldown_bars=max(strategy.entry.cooldown_bars, 1)
        )
    return snapshot


def _entry_admitted(
    snapshot: DeploymentSnapshot,
    *,
    product_id: str,
    notional: Decimal,
    risk_policy: RiskPolicyDefinition,
    portfolio: Sequence[DeploymentSnapshot],
    observation: EntryObservation | None = None,
    is_pyramid_add: bool = False,
) -> bool:
    """Return whether the active risk policy allows this sized entry."""
    return (
        _entry_verdict(
            snapshot,
            product_id=product_id,
            notional=notional,
            risk_policy=risk_policy,
            portfolio=portfolio,
            observation=observation,
            is_pyramid_add=is_pyramid_add,
        ).decision
        is RiskDecision.ALLOW
    )


def _entry_verdict(
    snapshot: DeploymentSnapshot,
    *,
    product_id: str,
    notional: Decimal,
    risk_policy: RiskPolicyDefinition,
    portfolio: Sequence[DeploymentSnapshot],
    observation: EntryObservation | None = None,
    is_pyramid_add: bool = False,
) -> RiskVerdict:
    """Return the entry gate verdict for this sized order."""
    live_cash = live_capital_base(snapshot.deployment)
    if snapshot.deployment.mode is DeploymentMode.LIVE and live_cash is None:
        return RiskVerdict(
            decision=RiskDecision.DENY,
            reason_code=RiskReasonCode.VENUE_BALANCE_UNKNOWN,
            detail="Venue quote balance is unknown; new entries are disabled.",
        )
    verdict = evaluate_new_entry(
        risk_policy,
        mode=snapshot.deployment.mode,
        proposed=ProposedEntry(
            product_id=product_id,
            strategy_id=snapshot.deployment.strategy_id,
            notional=notional,
            is_pyramid_add=is_pyramid_add,
        ),
        snapshots=_portfolio_with_current(portfolio, snapshot),
        live_quote_cash=live_cash,
        observation=observation,
        portfolio=portfolio_risk_for(snapshot.deployment),
    )
    scope = current_trade_reason_scope()
    if scope is not None:
        scope.remember_risk(verdict)
    note_risk(verdict)
    return verdict


def _document_open_book_count(snapshot: DeploymentSnapshot) -> int:
    """Count distinct product books and pending entries inside this document."""
    products: set[str] = set()
    for position in snapshot_positions(snapshot):
        products.add(resolved_product_id(position.product_id, snapshot.deployment))
    if snapshot.instrument_runtimes:
        for runtime in snapshot.instrument_runtimes:
            if runtime.phase in {
                RuntimePhase.OPEN,
                RuntimePhase.PENDING_ENTRY,
                RuntimePhase.PENDING_EXIT,
            }:
                products.add(runtime.product_id)
        return len(products)
    if snapshot.position is not None or snapshot.deployment.phase in {
        RuntimePhase.OPEN,
        RuntimePhase.PENDING_ENTRY,
        RuntimePhase.PENDING_EXIT,
    }:
        products.add(snapshot.deployment.product_id)
    return len(products)


def _portfolio_with_current(
    portfolio: Sequence[DeploymentSnapshot],
    snapshot: DeploymentSnapshot,
) -> tuple[DeploymentSnapshot, ...]:
    """Overlay this deployment's latest snapshot onto occupied peers."""
    others = tuple(item for item in portfolio if item.deployment.id != snapshot.deployment.id)
    return (*others, snapshot)


async def _cancel_open_orders(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
) -> None:
    """Cancel every active order the venue can identify before a marketable exit."""
    for order in snapshot.orders:
        if order.status in _ACTIVE and order.venue_order_id:
            await _cancel_one_order(order, broker=broker, store=store)


async def _cancel_one_order(
    order: Order, *, broker: Broker, store: ExecutionStore
) -> DeploymentSnapshot:
    """Cancel one active order when the venue id is known, confirming via GET order.

    An order whose cancel the venue already accepted is only re-checked with GET order,
    never re-cancelled, so a slow venue cancel cannot turn into a retry storm. A transport
    failure leaves the order unchanged for the next cycle.
    """
    if order.venue_order_id is None:
        return await store.get_deployment(order.deployment_id)
    try:
        if order.reject_reason == CANCEL_PENDING_REASON:
            result = await broker.get_order(
                venue_order_id=order.venue_order_id, client_order_id=order.client_order_id
            )
            if result.status in _ACTIVE:
                result = replace(result, reject_reason=CANCEL_PENDING_REASON)
        else:
            result = await broker.cancel_order(
                venue_order_id=order.venue_order_id,
                client_order_id=order.client_order_id,
            )
    except BrokerError:
        return await store.get_deployment(order.deployment_id)
    await store.save_order(
        replace(
            order,
            status=result.status,
            updated_at=utc_now(),
            reject_reason=result.reject_reason,
        )
    )
    return await store.get_deployment(order.deployment_id)


def _bar_observation(
    *,
    product_id: str,
    candle: Candle,
    proposed_price: Decimal | None,
    marks: Mapping[str, Decimal] | None,
) -> EntryObservation:
    """Build breaker observation from this bar's close plus any sibling marks."""
    combined = dict(marks) if marks is not None else {}
    combined[product_id] = candle.close
    return EntryObservation(
        as_of=utc_now(),
        proposed_price=proposed_price,
        reference_price=candle.close,
        marks=combined,
    )


async def _apply_circuit_breakers(
    snapshot: DeploymentSnapshot,
    *,
    candle: Candle,
    store: ExecutionStore,
    risk_policy: RiskPolicyDefinition,
    portfolio: Sequence[DeploymentSnapshot],
    marks: Mapping[str, Decimal] | None,
) -> DeploymentSnapshot:
    """Pause when daily-loss or drawdown has already tripped before a new entry."""
    live_cash = live_capital_base(snapshot.deployment)
    verdict = evaluate_runtime_breakers(
        risk_policy,
        mode=snapshot.deployment.mode,
        snapshot=snapshot,
        snapshots=_portfolio_with_current(portfolio, snapshot),
        live_quote_cash=live_cash,
        observation=_bar_observation(
            product_id=snapshot.deployment.product_id,
            candle=candle,
            proposed_price=None,
            marks=marks,
        ),
    )
    if verdict.decision is RiskDecision.ALLOW:
        return snapshot
    if not pauses_risk_increasing(verdict.reason_code):
        return snapshot
    note_breaker(verdict)
    return await _pause_for_breaker(snapshot, store=store, portfolio=portfolio, verdict=verdict)


async def _pause_for_breaker(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    portfolio: Sequence[DeploymentSnapshot],
    verdict: RiskVerdict,
) -> DeploymentSnapshot:
    """Pause this book, and the whole mode when the daily-loss kill trips."""
    detail = breaker_pause_detail(verdict.reason_code, verdict.detail)
    latched_daily = verdict.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT
    latched_dd = verdict.reason_code is RiskReasonCode.STRATEGY_DRAWDOWN_LIMIT
    if latched_daily or latched_dd:
        stamped = with_runtime(
            snapshot.deployment,
            updated_at=utc_now(),
            daily_loss_latched=True if latched_daily else None,
            drawdown_latched=True if latched_dd else None,
        )
        await store.save_deployment(stamped)
        snapshot = await store.get_deployment(snapshot.deployment.id)
    if verdict.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT:
        await _pause_mode_running(
            store=store,
            mode=snapshot.deployment.mode,
            portfolio=_portfolio_with_current(portfolio, snapshot),
            detail=detail,
        )
        return await store.get_deployment(snapshot.deployment.id)
    return await _pause(snapshot, store=store, detail=detail)


async def _pause_mode_running(
    *,
    store: ExecutionStore,
    mode: DeploymentMode,
    portfolio: Sequence[DeploymentSnapshot],
    detail: str,
) -> None:
    """Pause every running deployment in this mode; exits on paused books continue."""
    for item in portfolio:
        deployment = item.deployment
        if deployment.mode is not mode or deployment.status is not DeploymentStatus.RUNNING:
            continue
        paused = with_runtime(
            deployment,
            updated_at=utc_now(),
            status=DeploymentStatus.PAUSED,
            mismatch_detail=detail,
        )
        await store.save_deployment(paused)


async def _pause(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    detail: str,
    phase: RuntimePhase | None = None,
) -> DeploymentSnapshot:
    """Pause when venue state cannot be reconciled safely.

    A STOPPED book (managed shutdown or flatten in progress) keeps its STOPPED status and
    only records the detail: re-pausing it would hand a requested stop back to the
    running/paused protection path, which may rest new brackets.
    """
    status = DeploymentStatus.PAUSED
    if snapshot.deployment.status is DeploymentStatus.STOPPED:
        status = DeploymentStatus.STOPPED
    paused = with_runtime(
        snapshot.deployment,
        updated_at=utc_now(),
        status=status,
        mismatch_detail=detail,
        phase=snapshot.deployment.phase if phase is None else phase,
        pending_entry_bars=0 if phase is RuntimePhase.FLAT else None,
        clear_pending_levels=phase is RuntimePhase.FLAT,
    )
    await store.save_deployment(paused)
    return await store.get_deployment(snapshot.deployment.id)


def _unsettled_fill_evidence(snapshot: DeploymentSnapshot) -> bool:
    """Detect unapplied economics and terminal orders still awaiting their fills."""
    if any(fill.economics_applied_at is None for fill in snapshot.fills):
        return True
    for order in snapshot.orders:
        if order.status is not OrderStatus.FILLED:
            continue
        covered = max(order.quantity, order.filled_quantity)
        if applied_fill_quantity(snapshot, order.id) < covered:
            return True
    return False


async def settle_stopped_book(
    snapshot: DeploymentSnapshot, *, store: ExecutionStore
) -> DeploymentSnapshot:
    """Finish a requested flatten once every book is flat and idle."""
    return await _settle_flat_book(snapshot, store=store)


async def _settle_flat_book(
    snapshot: DeploymentSnapshot, *, store: ExecutionStore
) -> DeploymentSnapshot:
    """Finish a requested flatten and retire position-only pause details once flat.

    A flatten that reaches flat with nothing working ends STOPPED/FLAT with no detail,
    even when an earlier fault had paused it. On other books a detail that only described
    the open position's protection or exit is overwritten (paused) or cleared.
    """
    deployment = snapshot.deployment
    if not flat_and_idle(snapshot) or _unsettled_fill_evidence(snapshot):
        return snapshot
    # Fill-ledger projection faults are not proof of zero venue inventory.
    if (deployment.mismatch_detail or "").startswith("Entry fill is missing"):
        return snapshot
    if flatten_requested(snapshot) and deployment.status in {
        DeploymentStatus.PAUSED,
        DeploymentStatus.STOPPED,
    }:
        if deployment.status is DeploymentStatus.STOPPED and deployment.mismatch_detail is None:
            return snapshot
        settled = with_runtime(
            deployment,
            updated_at=utc_now(),
            status=DeploymentStatus.STOPPED,
            clear_mismatch=True,
            pending_entry_bars=0,
        )
        await store.save_deployment(settled)
        await record_execution_audit(
            action="flatten_settled",
            outcome=AuditEventOutcome.SUCCESS,
            detail=(
                f"deployment_id={deployment.id} previous_status={deployment.status.value}: "
                "requested flatten reached flat with no working orders; status stopped."
            ),
            product_id=deployment.product_id,
        )
        return await store.get_deployment(deployment.id)
    if not stale_position_fault(deployment.mismatch_detail):
        return snapshot
    if deployment.status is DeploymentStatus.PAUSED:
        refreshed = with_runtime(
            deployment, updated_at=utc_now(), mismatch_detail=FLAT_AFTER_FAULT_DETAIL
        )
    else:
        refreshed = with_runtime(deployment, updated_at=utc_now(), clear_mismatch=True)
    await store.save_deployment(refreshed)
    return await store.get_deployment(deployment.id)


async def _persist_performance(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    mark_price: Decimal,
    marks: Mapping[str, Decimal] | None = None,
    product_id: str,
) -> DeploymentSnapshot:
    """Stamp inventory cost, equity, HWM, and UTC day-open without changing phase."""
    combined: dict[str, Decimal] = dict(marks) if marks is not None else {}
    combined[product_id] = mark_price
    marked = refresh_performance(snapshot, marks=combined, now=utc_now())
    if marked == snapshot.deployment:
        return snapshot
    await store.save_deployment(marked)
    return await store.get_deployment(snapshot.deployment.id)


async def _persist_runtime(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    last_evaluated_bar: datetime,
) -> DeploymentSnapshot:
    """Write runtime fields without clobbering a concurrent operator status."""
    latest = await store.get_deployment(snapshot.deployment.id)
    operator = latest.deployment.status
    status = operator
    if (
        snapshot.deployment.status is DeploymentStatus.PAUSED
        and operator is not DeploymentStatus.STOPPED
    ):
        status = DeploymentStatus.PAUSED
    updated = with_runtime(
        snapshot.deployment,
        updated_at=utc_now(),
        last_evaluated_bar=last_evaluated_bar,
        status=status,
    )
    await store.save_deployment(updated)
    return await store.get_deployment(updated.id)


def _active_side(orders: Sequence[Order], side: OrderSide) -> Order | None:
    """Return the first active order on one side, if any."""
    return next(
        (order for order in orders if order.status in _ACTIVE and order.side is side),
        None,
    )


def _active_entry(snapshot: DeploymentSnapshot) -> Order | None:
    """Return the working entry order, preferring purpose-tagged intents."""
    entry_ids = {intent.id for intent in snapshot.intents if intent.purpose is IntentPurpose.ENTRY}
    if entry_ids:
        return next(
            (
                order
                for order in snapshot.orders
                if order.intent_id in entry_ids and order.status in _ACTIVE
            ),
            None,
        )
    return next(
        (
            order
            for order in snapshot.orders
            if order.status in _ACTIVE
            and order.kind in {OrderKind.POST_ONLY_LIMIT, OrderKind.MARKETABLE}
        ),
        None,
    )


async def _await_maker_limit(
    broker: Broker,
    *,
    product_id: str,
    mark: Decimal,
    side: OrderSide,
) -> Decimal:
    """Await maker_limit_price whether the broker implements it as async or sync.

    CoinbaseRestBroker keeps a sync implementation (owned by a sibling slice); paper
    and tests may be sync or async. Network I/O wrapping stays outside this helper.
    """
    result = broker.maker_limit_price(product_id=product_id, mark=mark, side=side)
    if inspect.isawaitable(result):
        awaited = await result
        if isinstance(awaited, Decimal):
            return awaited
        raise BrokerError("Maker entry price is unavailable.")
    if isinstance(result, Decimal):
        return result
    raise BrokerError("Maker entry price is unavailable.")


def _signal_intent_key(
    deployment_id: UUID, purpose: IntentPurpose, candle_starts_at: datetime, product_id: str
) -> str:
    """Stable command/signal identity used to deduplicate order intents."""
    stamp = candle_starts_at.strftime("%Y%m%dT%H%M")
    return f"{deployment_id}:{purpose.value}:{product_id}:{stamp}"[:128]


def _entry_fee_rate(deployment: Deployment, *, fee_profile: FeeProfile | None = None) -> Decimal:
    """Size entries with paper assumptions or the live Coinbase maker tier when available."""
    if deployment.mode is DeploymentMode.PAPER:
        maker_fee_rate, _taker_fee_rate = effective_paper_fee_rates(
            deployment.paper_maker_fee_rate, deployment.paper_taker_fee_rate
        )
        return maker_fee_rate
    if fee_profile is not None:
        return fee_profile.maker_fee_rate
    return PAPER_MAKER_FEE_RATE
