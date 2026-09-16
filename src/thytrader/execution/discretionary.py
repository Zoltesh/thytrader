"""On-demand long or short entries with SL/TP through intent, risk, and the broker."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation
import re
from typing import TYPE_CHECKING

from thytrader.execution.freshness import entry_prerequisites, marketable_quote_mark
from thytrader.execution.geometry import (
    bracket_error_detail,
    bracket_is_valid,
    entry_order_side,
    exit_order_side,
    paper_stop_fill_price,
    paper_stop_hit,
    parse_position_side,
)
from thytrader.execution.ids import utc_now, uuid7
from thytrader.execution.ledger import resolve_paper_fee_schedule
from thytrader.execution.loop import (
    _active_entry,
    _active_side,
    _cancel_open_orders,
    _ensure_exit_protection,
    _ensure_live_bracket,
    _ensure_take_profit,
    _flatten_pending,
    _match_resting_orders,
    _pause,
    _pause_mode_running,
    _persist_runtime,
    apply_fill,
)
from thytrader.execution.models import (
    Deployment,
    DeploymentKind,
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
from thytrader.execution.paper import bind_paper_broker_fees
from thytrader.execution.reconcile import reconcile_open_orders
from thytrader.execution.sizing import quantize_to_increment
from thytrader.execution.submit import submit_intent
from thytrader.execution.trade_reason_scope import (
    discretionary_trade_reason_scope,
    trade_reason_scope,
)
from thytrader.market_data.models import EXECUTION_TIMEFRAMES, parse_candle_interval
from thytrader.risk.breakers import EntryObservation
from thytrader.risk.gate import ProposedEntry, evaluate_new_deployment, evaluate_new_entry
from thytrader.risk.models import RiskDecision, RiskReasonCode, RiskVerdict, pauses_risk_increasing
from thytrader.risk.store import load_effective_policy

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.execution.broker import Broker
    from thytrader.execution.store import ExecutionStore
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.market_data.service import MarketDataService
    from thytrader.memory.store import ExperientialMemoryStore
    from thytrader.risk.store import RiskPolicyStore

_PRODUCT = re.compile(r"^[A-Z0-9]{2,20}-USD$")
_IN_MARKET = {RuntimePhase.OPEN, RuntimePhase.PENDING_ENTRY, RuntimePhase.PENDING_EXIT}
_OCCUPIED = {DeploymentStatus.RUNNING, DeploymentStatus.PAUSED}


@dataclass(frozen=True, slots=True)
class DiscretionaryOrderRequest:
    """One validated on-demand long or short the API or CLI wants to rest."""

    mode: DeploymentMode
    product_id: str
    entry_kind: OrderKind
    stop_price: Decimal
    take_profit_price: Decimal
    origin: IntentOrigin
    idempotency_key: str
    timeframe: str
    side: PositionSide
    quantity: Decimal | None
    quote_notional: Decimal | None
    limit_price: Decimal | None
    paper_starting_cash: Decimal | None
    paper_maker_fee_rate: Decimal | None
    paper_taker_fee_rate: Decimal | None
    note: str | None = None


def parse_discretionary_request(
    *,
    mode: str,
    product_id: str,
    entry_kind: str,
    stop_price: str,
    take_profit_price: str,
    origin: str,
    idempotency_key: str,
    timeframe: str = "5m",
    side: str = "long",
    quantity: str | None = None,
    quote_notional: str | None = None,
    limit_price: str | None = None,
    paper_starting_cash: str | None = None,
    paper_maker_fee_rate: str | None = None,
    paper_taker_fee_rate: str | None = None,
    note: str | None = None,
) -> DiscretionaryOrderRequest:
    """Parse decimal strings and reject illegal combinations before risk or persist."""
    parsed_mode = _parse_mode(mode)
    parsed_kind = _parse_entry_kind(entry_kind)
    parsed_origin = _parse_origin(origin)
    parsed_timeframe = _parse_timeframe(timeframe)
    try:
        parsed_side = parse_position_side(side)
    except ValueError as error:
        raise ExecutionConflictError(str(error)) from error
    if not _PRODUCT.match(product_id):
        raise ExecutionConflictError("product_id must be a BASE-USD spot id.")
    if not idempotency_key or len(idempotency_key) > 128:
        raise ExecutionConflictError("idempotency_key must be 1-128 characters.")
    qty = _optional_positive_decimal(quantity, field="quantity")
    notional = _optional_positive_decimal(quote_notional, field="quote_notional")
    if (qty is None) == (notional is None):
        raise ExecutionConflictError("Provide exactly one of quantity or quote_notional.")
    limit = _optional_positive_decimal(limit_price, field="limit_price")
    if parsed_kind is OrderKind.POST_ONLY_LIMIT and limit is None:
        raise ExecutionConflictError("Post-only entries require limit_price.")
    cash = _optional_positive_decimal(paper_starting_cash, field="paper_starting_cash")
    if parsed_mode is DeploymentMode.LIVE and cash is not None:
        raise ExecutionConflictError("Live orders do not accept paper_starting_cash.")
    maker, taker = _parse_paper_fee_rates(
        mode=parsed_mode,
        maker_fee_rate=paper_maker_fee_rate,
        taker_fee_rate=paper_taker_fee_rate,
    )
    return DiscretionaryOrderRequest(
        mode=parsed_mode,
        product_id=product_id,
        entry_kind=parsed_kind,
        stop_price=_require_positive_decimal(stop_price, field="stop_price"),
        take_profit_price=_require_positive_decimal(take_profit_price, field="take_profit_price"),
        origin=parsed_origin,
        idempotency_key=idempotency_key,
        timeframe=parsed_timeframe,
        side=parsed_side,
        quantity=qty,
        quote_notional=notional,
        limit_price=limit,
        paper_starting_cash=cash,
        paper_maker_fee_rate=maker,
        paper_taker_fee_rate=taker,
        note=_optional_note(note),
    )


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
) -> DeploymentSnapshot:
    """Persist a discretionary intent, submit once, and reconcile timeouts without retry."""
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
        live_quote_cash=live_quote_cash,
        entry_price=sized.entry_price,
        reference_price=mark_candle.close,
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
    await store.save_deployment(pending)
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
    interval = parse_candle_interval(request.timeframe)
    preview = await market_data.get_preview(request.product_id, interval)
    if preview.product.product_id != request.product_id or not preview.product.trading_enabled:
        raise ExecutionConflictError("Product is not a tradable USD spot market.")
    candles = preview.quality.candles
    if not candles:
        raise ExecutionConflictError("A closed mark candle is required before placing an order.")
    mark_candle = candles[-1]
    verdict = entry_prerequisites(
        product=preview.product,
        candle=mark_candle,
        now=utc_now(),
        timeframe=request.timeframe,
    )
    if verdict.decision is RiskDecision.DENY:
        raise ExecutionConflictError(verdict.detail)
    return preview.product, mark_candle


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


async def _book_for_entry(
    store: ExecutionStore,
    *,
    request: DiscretionaryOrderRequest,
    risk_store: RiskPolicyStore | None,
    notional: Decimal,
    live_quote_cash: Decimal | None,
    entry_price: Decimal,
    reference_price: Decimal,
) -> DeploymentSnapshot:
    """Reuse a flat running book or create one after the risk gate admits it."""
    existing = await store.list_deployments()
    reusable = _reusable_book(existing, product_id=request.product_id, mode=request.mode)
    if reusable is not None:
        _require_matching_paper_fees(reusable, request)
        snapshot = await store.get_deployment(reusable.id)
        await _require_entry_admission(
            risk_store,
            store=store,
            request=request,
            snapshot=snapshot,
            notional=notional,
            live_quote_cash=live_quote_cash,
            deployments=existing,
            entry_price=entry_price,
            reference_price=reference_price,
        )
        return snapshot
    _reject_occupied_book(existing, product_id=request.product_id, mode=request.mode)
    await _require_book_admission(
        risk_store,
        request=request,
        deployments=existing,
    )
    candidate = _new_discretionary_book(request, live_quote_cash=live_quote_cash)
    await _require_entry_admission(
        risk_store,
        store=store,
        request=request,
        snapshot=DeploymentSnapshot(deployment=candidate),
        notional=notional,
        live_quote_cash=live_quote_cash,
        deployments=existing,
        entry_price=entry_price,
        reference_price=reference_price,
    )
    created = await store.create_deployment(candidate)
    return await store.get_deployment(created.id)


def _new_discretionary_book(
    request: DiscretionaryOrderRequest, *, live_quote_cash: Decimal | None
) -> Deployment:
    """Build a flat discretionary book that has not been persisted yet."""
    now = utc_now()
    try:
        maker_fee_rate, taker_fee_rate = resolve_paper_fee_schedule(
            live=request.mode is DeploymentMode.LIVE,
            maker_fee_rate=request.paper_maker_fee_rate,
            taker_fee_rate=request.paper_taker_fee_rate,
        )
    except ValueError as error:
        raise ExecutionConflictError(str(error)) from error
    return Deployment(
        id=uuid7(now),
        strategy_fingerprint=None,
        strategy_id=None,
        product_id=request.product_id,
        mode=request.mode,
        status=DeploymentStatus.RUNNING,
        paper_starting_cash=request.paper_starting_cash,
        paper_maker_fee_rate=maker_fee_rate,
        paper_taker_fee_rate=taker_fee_rate,
        cash=_initial_cash(request, live_quote_cash=live_quote_cash),
        phase=RuntimePhase.FLAT,
        created_at=now,
        updated_at=now,
        kind=DeploymentKind.DISCRETIONARY,
        timeframe=request.timeframe,
    )


def _initial_cash(
    request: DiscretionaryOrderRequest, *, live_quote_cash: Decimal | None
) -> Decimal:
    """Paper uses starting cash; live uses remaining quote when known."""
    if request.mode is DeploymentMode.PAPER:
        cash = request.paper_starting_cash
        if cash is None or cash <= 0:
            raise ExecutionConflictError(
                "Paper discretionary orders require positive starting cash."
            )
        return cash
    if live_quote_cash is None:
        return Decimal("0")
    return live_quote_cash


def _reusable_book(
    deployments: tuple[Deployment, ...],
    *,
    product_id: str,
    mode: DeploymentMode,
) -> Deployment | None:
    """Return a flat running discretionary book that can accept a new entry."""
    matches = [
        item
        for item in deployments
        if item.kind is DeploymentKind.DISCRETIONARY
        and item.product_id == product_id
        and item.mode is mode
        and item.status is DeploymentStatus.RUNNING
        and item.phase is RuntimePhase.FLAT
    ]
    return matches[0] if matches else None


def _reject_occupied_book(
    deployments: tuple[Deployment, ...],
    *,
    product_id: str,
    mode: DeploymentMode,
) -> None:
    """Conflict when another occupied discretionary book already owns this product."""
    occupied = [
        item
        for item in deployments
        if item.kind is DeploymentKind.DISCRETIONARY
        and item.product_id == product_id
        and item.mode is mode
        and item.status in _OCCUPIED
    ]
    if occupied:
        raise ExecutionConflictError(
            "An occupied discretionary book already exists for this product and mode."
        )


async def _require_book_admission(
    risk_store: RiskPolicyStore | None,
    *,
    request: DiscretionaryOrderRequest,
    deployments: tuple[Deployment, ...],
) -> None:
    """Fail closed when the registry rejects a new discretionary book."""
    if request.mode is DeploymentMode.PAPER and (
        request.paper_starting_cash is None or request.paper_starting_cash <= 0
    ):
        raise ExecutionConflictError("Paper discretionary orders require positive starting cash.")
    active = await load_effective_policy(risk_store)
    verdict = evaluate_new_deployment(
        active.definition,
        mode=request.mode,
        product_id=request.product_id,
        strategy_id=None,
        paper_starting_cash=request.paper_starting_cash,
        deployments=deployments,
        policy_source=active.source,
    )
    if verdict.decision is RiskDecision.DENY:
        raise ExecutionConflictError(verdict.detail)


async def _require_entry_admission(
    risk_store: RiskPolicyStore | None,
    *,
    store: ExecutionStore,
    request: DiscretionaryOrderRequest,
    snapshot: DeploymentSnapshot,
    notional: Decimal,
    live_quote_cash: Decimal | None,
    deployments: tuple[Deployment, ...],
    entry_price: Decimal,
    reference_price: Decimal,
) -> None:
    """Fail closed when the registry rejects this sized entry."""
    active = await load_effective_policy(risk_store)
    peers = await _occupied_snapshots(
        store, deployments=deployments, exclude_id=snapshot.deployment.id
    )
    live_cash = live_quote_cash if request.mode is DeploymentMode.LIVE else None
    verdict = evaluate_new_entry(
        active.definition,
        mode=request.mode,
        proposed=ProposedEntry(
            product_id=request.product_id,
            strategy_id=None,
            notional=notional,
        ),
        snapshots=(*peers, snapshot),
        live_quote_cash=live_cash,
        observation=EntryObservation(
            as_of=utc_now(),
            proposed_price=entry_price,
            reference_price=reference_price,
            marks={request.product_id: reference_price},
        ),
    )
    if verdict.decision is RiskDecision.ALLOW:
        return
    await _pause_on_breaker(
        store=store,
        request=request,
        snapshot=snapshot,
        deployments=deployments,
        peers=peers,
        verdict=verdict,
    )
    raise ExecutionConflictError(verdict.detail)


async def _pause_on_breaker(
    *,
    store: ExecutionStore,
    request: DiscretionaryOrderRequest,
    snapshot: DeploymentSnapshot,
    deployments: tuple[Deployment, ...],
    peers: tuple[DeploymentSnapshot, ...],
    verdict: RiskVerdict,
) -> None:
    """Pause this book, or the whole mode on daily-loss, when the gate trips."""
    if not pauses_risk_increasing(verdict.reason_code):
        return
    detail = f"{verdict.reason_code.value}: {verdict.detail}"
    if verdict.reason_code is RiskReasonCode.DAILY_LOSS_LIMIT:
        await _pause_mode_running(
            store=store,
            mode=request.mode,
            portfolio=(*peers, snapshot),
            detail=detail,
        )
        return
    if any(item.id == snapshot.deployment.id for item in deployments):
        await _pause(snapshot, store=store, detail=detail)


async def _occupied_snapshots(
    store: ExecutionStore,
    *,
    deployments: tuple[Deployment, ...],
    exclude_id: UUID,
) -> tuple[DeploymentSnapshot, ...]:
    """Load occupied peer books so breakers see fills, orders, and inventory."""
    peers: list[DeploymentSnapshot] = []
    for item in deployments:
        if item.id == exclude_id or item.status not in _OCCUPIED:
            continue
        peers.append(await store.get_deployment(item.id))
    return tuple(peers)


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


def _parse_mode(value: str) -> DeploymentMode:
    """Parse paper or live."""
    try:
        return DeploymentMode(value)
    except ValueError as error:
        raise ExecutionConflictError("mode must be paper or live.") from error


def _parse_entry_kind(value: str) -> OrderKind:
    """Parse maker or marketable entry style."""
    try:
        kind = OrderKind(value)
    except ValueError as error:
        raise ExecutionConflictError("entry_kind must be post_only_limit or marketable.") from error
    if kind not in {OrderKind.POST_ONLY_LIMIT, OrderKind.MARKETABLE}:
        raise ExecutionConflictError("entry_kind must be post_only_limit or marketable.")
    return kind


def _parse_origin(value: str) -> IntentOrigin:
    """Parse human or agent origin."""
    try:
        origin = IntentOrigin(value)
    except ValueError as error:
        raise ExecutionConflictError("origin must be human or agent.") from error
    if origin is IntentOrigin.RUNTIME:
        raise ExecutionConflictError("origin must be human or agent.")
    return origin


def _parse_timeframe(value: str) -> str:
    """Allow only ingested venue execution clocks."""
    allowed = ", ".join(EXECUTION_TIMEFRAMES)
    try:
        interval = parse_candle_interval(value)
    except ValueError as error:
        raise ExecutionConflictError(
            f"Discretionary orders require an ingested venue timeframe: {allowed}."
        ) from error
    if not interval.execution_supported:
        raise ExecutionConflictError(
            f"Discretionary orders require an ingested venue timeframe: {allowed}."
        )
    return interval.value


def _require_positive_decimal(value: str, *, field: str) -> Decimal:
    """Parse a required positive finite decimal string."""
    parsed = _parse_decimal(value, field=field)
    if parsed <= 0:
        raise ExecutionConflictError(f"{field} must be a positive decimal string.")
    return parsed


def _require_matching_paper_fees(book: Deployment, request: DiscretionaryOrderRequest) -> None:
    """Refuse a second paper ticket that would silently change the book's fee assumptions."""
    if request.mode is not DeploymentMode.PAPER:
        return
    if request.paper_maker_fee_rate is None and request.paper_taker_fee_rate is None:
        return
    if (
        book.paper_maker_fee_rate != request.paper_maker_fee_rate
        or book.paper_taker_fee_rate != request.paper_taker_fee_rate
    ):
        raise ExecutionConflictError(
            "Paper fee rates are fixed on the existing discretionary book."
        )


def _parse_paper_fee_rates(
    *,
    mode: DeploymentMode,
    maker_fee_rate: str | None,
    taker_fee_rate: str | None,
) -> tuple[Decimal | None, Decimal | None]:
    """Parse optional paper fee strings without inventing live venue rates.

    Omitted paper rates stay unset so a new book can default and a reused book
    can keep its stored assumptions.
    """
    maker = _optional_non_negative_decimal(maker_fee_rate, field="maker_fee_rate")
    taker = _optional_non_negative_decimal(taker_fee_rate, field="taker_fee_rate")
    live = mode is DeploymentMode.LIVE
    if not live and (maker is None) != (taker is None):
        raise ExecutionConflictError(
            "Paper fee rates require both maker_fee_rate and taker_fee_rate."
        )
    if not live and (maker is None or taker is None):
        return None, None
    try:
        resolve_paper_fee_schedule(live=live, maker_fee_rate=maker, taker_fee_rate=taker)
    except ValueError as error:
        raise ExecutionConflictError(str(error)) from error
    if live:
        return None, None
    return maker, taker


def _optional_non_negative_decimal(value: str | None, *, field: str) -> Decimal | None:
    """Parse an optional non-negative finite decimal string."""
    if value is None or value == "":
        return None
    parsed = _parse_decimal(value, field=field)
    if parsed < 0:
        raise ExecutionConflictError(f"{field} must be a non-negative decimal string.")
    return parsed


def _optional_note(value: str | None) -> str | None:
    """Keep an optional place-order why-note, or omit blank text."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if len(stripped) > 4000:
        raise ExecutionConflictError("note must be at most 4000 characters.")
    return stripped


def _optional_positive_decimal(value: str | None, *, field: str) -> Decimal | None:
    """Parse an optional positive finite decimal string."""
    if value is None or value == "":
        return None
    parsed = _parse_decimal(value, field=field)
    if parsed <= 0:
        raise ExecutionConflictError(f"{field} must be a positive decimal string.")
    return parsed


def _parse_decimal(value: str, *, field: str) -> Decimal:
    """Parse one finite decimal string."""
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ExecutionConflictError(f"{field} must be a finite decimal string.") from error
    if not parsed.is_finite():
        raise ExecutionConflictError(f"{field} must be a finite decimal string.")
    return parsed
