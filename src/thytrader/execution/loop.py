"""One closed-candle cycle for a deployed strategy.

``process_closed_bar`` orchestrates the cycle and the open-position step. Entries,
exits, protection, residual flattening, circuit breakers and shared runtime operations
live in sibling modules; names other modules import from here are re-exported
(``__all__``).
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from thytrader.execution.breaker_pause import _apply_circuit_breakers, _pause_for_breaker
from thytrader.execution.capital import refresh_performance
from thytrader.execution.entry import _fail_closed_on_split_state, _maybe_enter
from thytrader.execution.entry_admission import _entry_admitted, _entry_verdict
from thytrader.execution.entry_reprice import _manage_working_entry
from thytrader.execution.entry_sizing import _runtime_for_admitted_entry, _size_entry_or_add
from thytrader.execution.exits import _evaluate_signal_exit, _mark_signal_exit
from thytrader.execution.futures_liquidation import paper_liquidation_if_due
from thytrader.execution.live_protection import (
    _apply_trailing,
    _ensure_exit_protection,
    _paper_stop_exit_if_hit,
    _protect_open_position,
)
from thytrader.execution.paper import bind_paper_broker_fees
from thytrader.execution.residual import (
    FLATTEN_AWAITING_EXECUTABLE_CONTEXT,
    _settle_flat_book,
    flatten_stopped_residual,
    settle_stopped_book,
)
from thytrader.execution.runtime_ops import (
    _ACTIVE,
    _active_entry,
    _adopt_venue_attached_child,
    _cancel_one_order,
    _match_resting_orders,
    _persist_runtime,
    cancel_resting_orders,
)
from thytrader.risk.accounting_evidence import accounting_snapshot
from thytrader.risk.models import compiled_default_risk_policy
from thytrader.trading.exposure import snapshot_has_residual_exposure
from thytrader.trading.ids import utc_now
from thytrader.trading.lifecycle import entries_allowed
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    ExecutionConflictError,
    ExecutionStoreError,
    IntentPurpose,
    RuntimePhase,
    is_venue_protection,
    with_runtime,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from decimal import Decimal

    from thytrader.exchanges.fees import FeeProfile
    from thytrader.execution.broker import Broker
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.trading.store import ExecutionStore

__all__ = [
    "FLATTEN_AWAITING_EXECUTABLE_CONTEXT",
    "_active_entry",
    "_apply_circuit_breakers",
    "_ensure_exit_protection",
    "_entry_admitted",
    "_entry_verdict",
    "_match_resting_orders",
    "_pause_for_breaker",
    "_persist_performance",
    "_persist_runtime",
    "_runtime_for_admitted_entry",
    "_size_entry_or_add",
    "cancel_resting_orders",
    "cancel_risk_increasing_orders",
    "flatten_stopped_residual",
    "maintain_open_inventory",
    "process_closed_bar",
    "settle_stopped_book",
]


_IN_MARKET = {RuntimePhase.OPEN, RuntimePhase.PENDING_EXIT}


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


async def cancel_risk_increasing_orders(
    snapshot: DeploymentSnapshot,
    *,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Cancel working entries while leaving protective brackets and stop-limits in place.

    An order whose intent is known and is not an ENTRY is never cancelled here, even on
    a book with no ENTRY intent at all (an adopted book, ADR 0124): a working marketable
    exit is risk-reducing. Only when no ENTRY intent is known does an order with an
    unknown intent still count as a possible entry.
    """
    purposes = {intent.id: intent.purpose for intent in snapshot.intents}
    entry_ids = {
        intent_id for intent_id, purpose in purposes.items() if purpose is IntentPurpose.ENTRY
    }
    for order in tuple(snapshot.orders):
        if order.status not in _ACTIVE:
            continue
        if is_venue_protection(order.kind):
            continue
        if purposes.get(order.intent_id, IntentPurpose.ENTRY) is not IntentPurpose.ENTRY:
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
            product_id=product.product_id,
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

    Paper follows the backtest's same-bar precedence (ADR 0083, ADR 0093): a paper futures
    liquidation (ADR 0129) first, then the protective
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
        liquidated = await paper_liquidation_if_due(
            snapshot,
            strategy=strategy,
            candle=candle,
            product=product,
            broker=broker,
            store=store,
            position=position,
        )
        if liquidated is not None:
            return liquidated
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
    now = utc_now()
    try:
        full = await accounting_snapshot(store, snapshot.deployment.id, as_of=now)
    except ExecutionStoreError:
        return snapshot
    current = await store.get_deployment(snapshot.deployment.id)
    if current.deployment.revision != full.deployment.revision:
        return current
    marked = refresh_performance(full, marks=combined, now=now)
    # Copy only metadata onto the fresh runtime view; never write cached sibling cash.
    marked = replace(
        current.deployment,
        inventory_cost=marked.inventory_cost,
        reserved_buying_power=marked.reserved_buying_power,
        performance_equity=marked.performance_equity,
        performance_capital_quote=marked.performance_capital_quote,
        performance_maximum_drawdown_fraction=marked.performance_maximum_drawdown_fraction,
        initial_equity=marked.initial_equity,
        baseline_equity=marked.baseline_equity,
        high_water_mark_equity=marked.high_water_mark_equity,
        risk_day_open_evidence=marked.risk_day_open_evidence,
        updated_at=marked.updated_at,
    )
    try:
        await store.save_deployment(marked, expected_revision=full.deployment.revision)
    except ExecutionConflictError:
        # A newly applied fill makes these derived values obsolete; the next cycle retries.
        return await store.get_deployment(snapshot.deployment.id)
    return await store.get_deployment(snapshot.deployment.id)
