"""Working-entry management for the closed-bar loop: wait, cancel, or reprice.

Counts wait bars on a resting entry, cancels it once the wait expires, and either
abandons it or rests a replacement post-only order for the remaining quantity after
re-sizing and risk re-admission, keeping the stop and target geometry legal.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.attached import remaining_quantity
from thytrader.execution.breaker_pause import _bar_observation, _pause_for_breaker
from thytrader.execution.broker import BrokerError
from thytrader.execution.decision_scope import note_entry_skip
from thytrader.execution.entry_admission import _entry_verdict
from thytrader.execution.entry_sizing import (
    _await_maker_limit,
    _signal_intent_key,
    _size_entry_or_add,
    _with_pending_levels,
)
from thytrader.execution.runtime_ops import (
    _active_entry,
    _cancel_one_order,
    _flatten_pending,
    _pause,
)
from thytrader.execution.signals import latest_atr
from thytrader.execution.submit import submit_intent
from thytrader.risk.models import RiskDecision, compiled_default_risk_policy, pauses_risk_increasing
from thytrader.strategies.models import atr_trailing_stop
from thytrader.trading.geometry import EntrySkipReason
from thytrader.trading.ids import utc_now
from thytrader.trading.lifecycle import can_reprice_risk_up
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentSnapshot,
    IntentPurpose,
    Order,
    OrderKind,
    OrderStatus,
    PositionSide,
    RuntimePhase,
    with_runtime,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from thytrader.exchanges.fees import FeeProfile
    from thytrader.execution.broker import Broker
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.risk.models import RiskPolicyDefinition
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.trading.store import ExecutionStore


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
    admitted = await _entry_verdict(
        snapshot,
        store=store,
        product_id=product.product_id,
        notional=entry_price * remaining_qty,
        quantity=remaining_qty,
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
