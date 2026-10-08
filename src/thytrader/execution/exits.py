"""Exits of the closed-bar execution loop: signal exits and marketable exits.

Signal-exit evaluation and marking, marketable exit submission with immediate-fill
application, pending-exit marking, and the fill-economics guard before an exit.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import pairwise
from typing import TYPE_CHECKING

from thytrader.evaluation.signal_evaluator import SignalEvaluationError
from thytrader.evaluation.trace import EntryConditionOutcome
from thytrader.execution.breaker_pause import _exit_cooldown
from thytrader.execution.decision_scope import note_evaluation_error, note_exit_evaluation
from thytrader.execution.exit_guards import (
    CANCEL_BEFORE_EXIT_DETAIL,
    MARKETABLE_EXIT_UNCONFIRMED_DETAIL,
    active_orders,
    cancel_pending,
    exit_fill_pending,
    exit_rejection,
    rejection_latched,
)
from thytrader.execution.fill_ledger import unprojected_inventory_products, unsettled_fill_evidence
from thytrader.execution.geometry import exit_order_side
from thytrader.execution.ids import utc_now
from thytrader.execution.models import (
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    IntentPurpose,
    Order,
    OrderKind,
    OrderStatus,
    Position,
    RuntimePhase,
    with_runtime,
)
from thytrader.execution.reconcile import FILLED_WITHOUT_REST_FILLS_DETAIL, ingest_order_fills
from thytrader.execution.runtime_ops import (
    _adopt_venue_attached_child,
    _cancel_open_orders,
    _pause,
    _pause_rejected,
    _reconcile_stopped_live,
    apply_fill,
)
from thytrader.execution.signals import evaluate_latest_signal_exit
from thytrader.execution.submit import submit_intent
from thytrader.market_data.models import parse_candle_interval
from thytrader.strategies.models import signal_exit_condition

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from decimal import Decimal

    from thytrader.execution.broker import Broker
    from thytrader.execution.store import ExecutionStore
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.strategies.models import StrategyDefinition


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


async def _mark_pending_exit(
    snapshot: DeploymentSnapshot, *, store: ExecutionStore
) -> DeploymentSnapshot:
    """Record that an exit order is working without changing cash or inventory."""
    pending = with_runtime(
        snapshot.deployment, updated_at=utc_now(), phase=RuntimePhase.PENDING_EXIT
    )
    await store.save_deployment(pending)
    return await store.get_deployment(snapshot.deployment.id)


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
    if detail := _exit_economics_fault(snapshot):
        return await _pause(snapshot, store=store, detail=detail)
    await _cancel_open_orders(snapshot, broker=broker, store=store)
    snapshot = await store.get_deployment(snapshot.deployment.id)
    if active_orders(snapshot) and all(cancel_pending(order) for order in active_orders(snapshot)):
        return await _mark_pending_exit(snapshot, store=store)
    snapshot = await _reconcile_stopped_live(snapshot, broker=broker, store=store)
    position = snapshot.position
    if position is None:
        return snapshot
    if detail := _exit_economics_fault(snapshot):
        return await _pause(snapshot, store=store, detail=detail)
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


def _exit_economics_fault(snapshot: DeploymentSnapshot) -> str | None:
    """Require complete fill economics and projected inventory before resizing or exiting."""
    if unprojected_inventory_products(snapshot):
        return "Applied fills contain unprojected inventory."
    if unsettled_fill_evidence(snapshot):
        return FILLED_WITHOUT_REST_FILLS_DETAIL
    return None
