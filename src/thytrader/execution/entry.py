"""Entries of the closed-bar execution loop: admission, sizing, submit, reprice.

Entry gates and risk verdicts, sizing (including pyramid adds), sized and repriced
entry submission, working-entry management, and the fail-closed split pending-entry
state check.
"""

from __future__ import annotations

from decimal import Decimal
import inspect
from typing import TYPE_CHECKING, Literal

from thytrader.execution.attached import remaining_quantity
from thytrader.execution.breaker_pause import (
    _bar_observation,
    _pause_for_breaker,
    _portfolio_with_current,
)
from thytrader.execution.broker import BrokerError
from thytrader.execution.capital import live_capital_base, live_sizing_cash
from thytrader.execution.decision_scope import (
    decision_observation_active,
    note_entry_block,
    note_entry_gate,
    note_entry_skip,
    note_evaluation,
    note_evaluation_error,
    note_freshness,
    note_risk,
)
from thytrader.execution.decisions import DecisionSkipReason
from thytrader.execution.freshness import entry_prerequisites, signal_still_valid
from thytrader.execution.geometry import EntrySkipReason, entry_order_side
from thytrader.execution.ids import utc_now
from thytrader.execution.ledger import PAPER_MAKER_FEE_RATE, effective_paper_fee_rates
from thytrader.execution.lifecycle import can_reprice_risk_up, entries_allowed
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    ExecutionStoreError,
    IntentPurpose,
    Order,
    OrderKind,
    OrderSide,
    OrderStatus,
    PositionSide,
    RuntimePhase,
    is_venue_protection,
    resolved_product_id,
    snapshot_positions,
    with_runtime,
)
from thytrader.execution.runtime_ops import (
    _active_entry,
    _cancel_one_order,
    _flatten_pending,
    _pause,
)
from thytrader.execution.signals import evaluate_latest_entry_evidence, latest_atr
from thytrader.execution.sizing import SizedEntry, size_entry_or_skip, size_pyramid_add_or_skip
from thytrader.execution.submit import submit_intent
from thytrader.execution.trade_reason_scope import current_trade_reason_scope
from thytrader.market_data.models import parse_candle_interval
from thytrader.research.multi_timeframe import htf_bars_closed_at_or_before, ltf_close
from thytrader.research.signal_evaluator import SignalEvaluationError
from thytrader.research.trace import EntryConditionOutcome
from thytrader.risk.breakers import EntryObservation
from thytrader.risk.gate import ProposedEntry, evaluate_new_entry
from thytrader.risk.models import (
    RiskDecision,
    RiskReasonCode,
    RiskVerdict,
    compiled_default_risk_policy,
    pauses_risk_increasing,
)
from thytrader.risk.portfolio_scope import portfolio_risk_for
from thytrader.strategies.models import atr_trailing_stop, can_pyramid_add

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


_STALE_SIGNAL_VERDICT = RiskVerdict(
    decision=RiskDecision.DENY,
    reason_code=RiskReasonCode.SIGNAL_STALE,
    detail="The closed signal bar is older than the maximum signal age; no entry was sent.",
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
    admitted = await _entry_verdict(
        snapshot,
        store=store,
        product_id=product.product_id,
        notional=sized.notional,
        quantity=sized.quantity,
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


async def _entry_admitted(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    product_id: str,
    notional: Decimal,
    risk_policy: RiskPolicyDefinition,
    portfolio: Sequence[DeploymentSnapshot],
    observation: EntryObservation | None = None,
    is_pyramid_add: bool = False,
    quantity: Decimal | None = None,
) -> bool:
    """Return whether the active risk policy allows this sized entry."""
    verdict = await _entry_verdict(
        snapshot,
        store=store,
        product_id=product_id,
        notional=notional,
        quantity=quantity,
        risk_policy=risk_policy,
        portfolio=portfolio,
        observation=observation,
        is_pyramid_add=is_pyramid_add,
    )
    return verdict.decision is RiskDecision.ALLOW


async def _entry_verdict(
    snapshot: DeploymentSnapshot,
    *,
    store: ExecutionStore,
    product_id: str,
    notional: Decimal,
    risk_policy: RiskPolicyDefinition,
    portfolio: Sequence[DeploymentSnapshot],
    observation: EntryObservation | None = None,
    is_pyramid_add: bool = False,
    quantity: Decimal | None = None,
) -> RiskVerdict:
    """Admit only against fresh full accounting, never the product view or cache."""
    observation = observation or EntryObservation(
        as_of=utc_now(), proposed_price=None, reference_price=None, marks={}
    )
    try:
        current_portfolio = await _portfolio_with_current(portfolio, snapshot, store=store)
    except ExecutionStoreError:
        return RiskVerdict(
            decision=RiskDecision.DENY,
            reason_code=RiskReasonCode.BREAKER_MARK_MISSING,
            detail="Fresh complete risk accounting evidence is unavailable; entries are disabled.",
        )
    current = next(
        item for item in current_portfolio if item.deployment.id == snapshot.deployment.id
    )
    live_cash = live_capital_base(current.deployment)
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
            quantity=quantity,
        ),
        snapshots=current_portfolio,
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
