"""On-demand long or short entries with SL/TP through intent, risk, and the broker."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from thytrader.execution.discretionary_book import _book_for_entry
from thytrader.execution.freshness import marketable_quote_mark
from thytrader.execution.live_protection import (
    _ensure_exit_protection,
    _ensure_live_bracket,
    _ensure_take_profit,
)
from thytrader.execution.mark_context import closed_mark_context
from thytrader.execution.paper import bind_paper_broker_fees
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.runtime_ops import (
    _active_entry,
    _active_side,
    _cancel_open_orders,
    _flatten_pending,
    _match_resting_orders,
    _pause,
    _persist_runtime,
    apply_fill,
)
from thytrader.execution.submit import submit_intent
from thytrader.memory.trade_reason_scope import discretionary_trade_reason_scope, trade_reason_scope
from thytrader.risk.store import load_effective_policy
from thytrader.trading.geometry import (
    bracket_error_detail,
    bracket_is_valid,
    entry_order_side,
    exit_order_side,
    paper_stop_fill_price,
    paper_stop_hit,
)
from thytrader.trading.ids import utc_now
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    ExecutionConflictError,
    IntentOrigin,
    IntentPurpose,
    OrderKind,
    OrderSide,
    OrderStatus,
    PositionSide,
    RuntimePhase,
    with_runtime,
)
from thytrader.trading.sizing import quantize_to_increment

if TYPE_CHECKING:
    from thytrader.execution.broker import Broker
    from thytrader.execution.discretionary_request import DiscretionaryOrderRequest
    from thytrader.execution.paper_fees import PaperFeeSource
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.market_data.service import MarketDataService
    from thytrader.memory.store import ExperientialMemoryStore
    from thytrader.risk.store import RiskPolicyStore
    from thytrader.trading.store import ExecutionStore

_IN_MARKET = {RuntimePhase.OPEN, RuntimePhase.PENDING_ENTRY, RuntimePhase.PENDING_EXIT}


async def place_discretionary_order(
    *,
    store: ExecutionStore,
    broker: Broker,
    market_data: MarketDataService,
    request: DiscretionaryOrderRequest,
    live_allowed: bool,
    risk_store: RiskPolicyStore | None = None,
    live_quote_cash: Decimal | None = None,
    live_base_available: Decimal | None = None,
    memory_store: ExperientialMemoryStore | None = None,
    paper_fee_source: PaperFeeSource | None = None,
) -> DeploymentSnapshot:
    """Persist a discretionary intent, submit once, and reconcile timeouts without retry.

    A new paper book that omits fee rates takes the account's rates from
    ``paper_fee_source``; a reused book keeps its stored rates.
    """
    _require_mode_prerequisites(request, live_allowed=live_allowed)
    existing = await store.get_intent_by_idempotency_key(request.idempotency_key)
    if existing is not None:
        return await store.get_deployment(existing.deployment_id)
    product, mark_candle = await _mark_context(market_data, request)
    mark = marketable_quote_mark(candle=mark_candle, venue_price=None)
    if request.entry_kind is OrderKind.POST_ONLY_LIMIT:
        mark = mark_candle.close
    sized = _size_entry(request, product=product, mark=mark)
    if (
        request.side is PositionSide.SHORT
        and request.mode is DeploymentMode.LIVE
        and (live_base_available is None or live_base_available < sized.quantity)
    ):
        raise ExecutionConflictError(
            "INSUFFICIENT_BASE_FOR_SPOT_SHORT: Coinbase spot shorts require available base."
        )
    snapshot = await _book_for_entry(
        store,
        request=request,
        risk_store=risk_store,
        notional=sized.notional,
        quantity=sized.quantity,
        live_quote_cash=live_quote_cash,
        entry_price=sized.entry_price,
        reference_price=mark_candle.close,
        paper_fee_source=paper_fee_source,
    )
    broker = bind_paper_broker_fees(broker, snapshot.deployment)
    pending = with_runtime(
        snapshot.deployment,
        updated_at=utc_now(),
        phase=RuntimePhase.PENDING_ENTRY,
        pending_entry_bars=0,
        pending_stop_price=sized.stop_price,
        pending_target_price=sized.take_profit_price,
        last_signal="discretionary",
        clear_mismatch=True,
    )
    # No intent or venue submission follows a lost candidate revision. Re-admission
    # must start from fresh evidence, not restore stale cash/status/lifecycle fields.
    await store.save_deployment(pending, expected_revision=snapshot.deployment.revision)
    active = await load_effective_policy(risk_store)
    scope = discretionary_trade_reason_scope(
        memory_store,
        policy=active.definition,
        timeframe=request.timeframe,
        note=request.note,
        note_origin=None if request.note is None else request.origin.value,
    )
    with trade_reason_scope(scope):
        order = await submit_intent(
            store=store,
            broker=broker,
            deployment_id=pending.id,
            product_id=request.product_id,
            purpose=IntentPurpose.ENTRY,
            side=entry_order_side(request.side),
            kind=request.entry_kind,
            quantity=sized.quantity,
            price=sized.entry_price,
            candle=mark_candle,
            stop_trigger_price=sized.stop_price,
            take_profit_price=sized.take_profit_price,
            origin=request.origin,
            idempotency_key=request.idempotency_key,
        )
        snapshot = await store.get_deployment(pending.id)
        if order.status is OrderStatus.UNKNOWN:
            snapshot = await reconcile_open_orders(
                snapshot,
                broker=broker,
                store=store,
                product_id=request.product_id,
                cooldown_bars=0,
            )
            order_status = next(
                (
                    item.status
                    for item in snapshot.orders
                    if item.client_order_id == order.client_order_id
                ),
                OrderStatus.UNKNOWN,
            )
            return await _after_entry_submit(
                snapshot,
                order_status=order_status,
                product=product,
                candle=mark_candle,
                broker=broker,
                store=store,
            )
        return await _after_entry_submit(
            snapshot,
            order_status=order.status,
            product=product,
            candle=mark_candle,
            broker=broker,
            store=store,
        )


async def process_discretionary_bar(
    snapshot: DeploymentSnapshot,
    *,
    product: MarketProduct,
    candles: tuple[Candle, ...],
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Advance one discretionary book by a newly closed candle without strategy signals."""
    if snapshot.deployment.status is DeploymentStatus.STOPPED or not candles:
        return snapshot
    broker = bind_paper_broker_fees(broker, snapshot.deployment)
    candle = candles[-1]
    deployment = snapshot.deployment
    if deployment.last_evaluated_bar == candle.starts_at:
        if deployment.phase in {RuntimePhase.OPEN, RuntimePhase.PENDING_EXIT}:
            return await _ensure_exit_protection(
                snapshot, candle=candle, product=product, broker=broker, store=store
            )
        return snapshot
    snapshot = await _match_resting_orders(
        snapshot,
        candle=candle,
        broker=broker,
        store=store,
        cooldown_bars=0,
        timeframe=deployment.timeframe or "1h",
    )
    snapshot = await _protect_discretionary(
        snapshot, candle=candle, product=product, broker=broker, store=store
    )
    return await _persist_runtime(snapshot, store=store, last_evaluated_bar=candle.starts_at)


@dataclass(frozen=True, slots=True)
class _SizedEntry:
    """Quantized size plus stop and take-profit prices."""

    quantity: Decimal
    notional: Decimal
    entry_price: Decimal
    stop_price: Decimal
    take_profit_price: Decimal


def _require_mode_prerequisites(request: DiscretionaryOrderRequest, *, live_allowed: bool) -> None:
    """Reject live without credentials."""
    if request.mode is DeploymentMode.LIVE and not live_allowed:
        raise ExecutionConflictError("Live trading requires configured Coinbase credentials.")
    if request.origin is IntentOrigin.RUNTIME:
        raise ExecutionConflictError("Discretionary orders require human or agent origin.")


async def _mark_context(
    market_data: MarketDataService,
    request: DiscretionaryOrderRequest,
) -> tuple[MarketProduct, Candle]:
    """Load product increments and the latest closed mark used for SL/TP validation."""
    return await closed_mark_context(
        market_data, product_id=request.product_id, timeframe=request.timeframe
    )


def _size_entry(
    request: DiscretionaryOrderRequest,
    *,
    product: MarketProduct,
    mark: Decimal,
) -> _SizedEntry:
    """Quantize size and prices; require side-correct stop and take-profit order."""
    raw_entry = request.limit_price if request.entry_kind is OrderKind.POST_ONLY_LIMIT else mark
    if raw_entry is None or raw_entry <= 0:
        raise ExecutionConflictError("Entry price must be a positive decimal.")
    entry = quantize_to_increment(raw_entry, product.price_increment)
    stop = quantize_to_increment(request.stop_price, product.price_increment)
    target = quantize_to_increment(
        request.take_profit_price, product.price_increment, rounding=ROUND_HALF_UP
    )
    if stop <= 0 or entry <= 0 or target <= 0:
        raise ExecutionConflictError(
            "Stop, entry, and take-profit must quantize to positive prices."
        )
    if not bracket_is_valid(side=request.side, entry=entry, stop=stop, take_profit=target):
        raise ExecutionConflictError(bracket_error_detail(request.side))
    quantity = _quantity_from_request(request, product=product, entry=entry)
    notional = quantity * entry
    if notional < product.quote_min_size:
        raise ExecutionConflictError("Notional is below the product quote minimum.")
    return _SizedEntry(
        quantity=quantity,
        notional=notional,
        entry_price=entry,
        stop_price=stop,
        take_profit_price=target,
    )


def _quantity_from_request(
    request: DiscretionaryOrderRequest,
    *,
    product: MarketProduct,
    entry: Decimal,
) -> Decimal:
    """Derive a base quantity from explicit size or quote notional."""
    if request.quantity is not None:
        quantity = quantize_to_increment(
            request.quantity, product.base_increment, rounding=ROUND_DOWN
        )
    elif request.quote_notional is not None:
        quantity = quantize_to_increment(
            request.quote_notional / entry, product.base_increment, rounding=ROUND_DOWN
        )
    else:
        raise ExecutionConflictError("Provide exactly one of quantity or quote_notional.")
    if quantity < product.base_min_size:
        raise ExecutionConflictError("Quantity is below the product base minimum.")
    return quantity


async def _after_entry_submit(
    snapshot: DeploymentSnapshot,
    *,
    order_status: OrderStatus,
    product: MarketProduct,
    candle: Candle,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Apply an immediate fill, pause on ambiguity, or leave a resting maker."""
    if order_status is OrderStatus.UNKNOWN:
        return await _pause(
            snapshot,
            store=store,
            detail="Entry submit is unconfirmed; reconcile before retrying.",
        )
    if order_status is OrderStatus.REJECTED:
        return await _flatten_pending(snapshot, store=store, cooldown_bars=0)
    current = await store.get_deployment(snapshot.deployment.id)
    if order_status is OrderStatus.FILLED:
        filled = next(
            (order for order in current.orders if order.status is OrderStatus.FILLED),
            None,
        )
        fill = next(
            (item for item in current.fills if filled is not None and item.order_id == filled.id),
            None,
        )
        if filled is None or fill is None:
            return await _pause(
                current, store=store, detail="Filled entry has no local fill to apply."
            )
        if fill.economics_applied_at is None:
            current = await apply_fill(
                current, fill=fill, order=filled, store=store, cooldown_bars=0
            )
        return await _ensure_exit_protection(
            current, candle=candle, product=product, broker=broker, store=store
        )
    return current


async def _protect_discretionary(
    snapshot: DeploymentSnapshot,
    *,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
) -> DeploymentSnapshot:
    """Leave resting entries, fire paper stops, or rest live/paper exits."""
    deployment = snapshot.deployment
    if deployment.phase is RuntimePhase.PENDING_ENTRY:
        return await _watch_pending_entry(snapshot, store=store)
    position = snapshot.position
    if deployment.phase not in _IN_MARKET or position is None:
        return snapshot
    live = deployment.mode is DeploymentMode.LIVE
    if live:
        return await _ensure_live_bracket(
            snapshot, candle=candle, product=product, broker=broker, store=store
        )
    if paper_stop_hit(side=position.side, candle=candle, stop_price=position.stop_price):
        return await _marketable_discretionary_exit(
            snapshot,
            candle=candle,
            product=product,
            broker=broker,
            store=store,
            price=paper_stop_fill_price(
                side=position.side, candle=candle, stop_price=position.stop_price
            ),
        )
    return await _ensure_take_profit(
        snapshot, candle=candle, product=product, broker=broker, store=store
    )


async def _watch_pending_entry(
    snapshot: DeploymentSnapshot, *, store: ExecutionStore
) -> DeploymentSnapshot:
    """Count wait bars; pause when the entry is unconfirmed; do not reprice."""
    open_entry = _active_entry(snapshot)
    if open_entry is None:
        return await _flatten_pending(snapshot, store=store, cooldown_bars=0)
    if open_entry.status is not OrderStatus.OPEN or open_entry.venue_order_id is None:
        return await _pause(
            snapshot,
            store=store,
            detail="Entry order is unconfirmed; reconcile before retrying.",
        )
    waited = with_runtime(
        snapshot.deployment,
        updated_at=utc_now(),
        pending_entry_bars=snapshot.deployment.pending_entry_bars + 1,
    )
    await store.save_deployment(waited)
    return await store.get_deployment(snapshot.deployment.id)


async def _marketable_discretionary_exit(
    snapshot: DeploymentSnapshot,
    *,
    candle: Candle,
    product: MarketProduct,
    broker: Broker,
    store: ExecutionStore,
    price: Decimal,
) -> DeploymentSnapshot:
    """Cancel resting exits, then submit a marketable cover of the open position."""
    position = snapshot.position
    if position is None:
        return snapshot
    await _cancel_open_orders(snapshot, broker=broker, store=store)
    snapshot = await store.get_deployment(snapshot.deployment.id)
    if _active_side(snapshot.orders, OrderSide.BUY) or _active_side(
        snapshot.orders, OrderSide.SELL
    ):
        return await _pause(
            snapshot,
            store=store,
            detail="Could not cancel resting orders before a marketable exit.",
        )
    order = await submit_intent(
        store=store,
        broker=broker,
        deployment_id=snapshot.deployment.id,
        product_id=product.product_id,
        purpose=IntentPurpose.STOP,
        side=exit_order_side(position.side),
        kind=OrderKind.MARKETABLE,
        quantity=position.quantity,
        price=price,
        candle=candle,
        origin=IntentOrigin.RUNTIME,
    )
    if order.status is OrderStatus.FILLED:
        current = await store.get_deployment(snapshot.deployment.id)
        fill = next((item for item in current.fills if item.order_id == order.id), None)
        if fill is None:
            return current
        return await apply_fill(current, fill=fill, order=order, store=store, cooldown_bars=0)
    return await _pause(
        await store.get_deployment(snapshot.deployment.id),
        store=store,
        detail="Marketable exit was not confirmed filled.",
    )
