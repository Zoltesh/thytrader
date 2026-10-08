"""New entries of the closed-bar execution loop: gates, signal, and sized submit.

Decides whether a bar may open a book or add to one, evaluates the entry signal and
its freshness, and rests the sized post-only entry once risk admits it. Also holds
the fail-closed split pending-entry state check. Sizing lives in ``entry_sizing``,
risk admission in ``entry_admission``, and working-entry repricing in ``entry_reprice``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from thytrader.evaluation.multi_timeframe import htf_bars_closed_at_or_before, ltf_close
from thytrader.evaluation.signal_evaluator import SignalEvaluationError
from thytrader.evaluation.trace import EntryConditionOutcome
from thytrader.execution.breaker_pause import _bar_observation, _pause_for_breaker
from thytrader.execution.broker import BrokerError
from thytrader.execution.capital import live_sizing_cash
from thytrader.execution.decision_scope import (
    decision_observation_active,
    note_entry_block,
    note_entry_gate,
    note_entry_skip,
    note_evaluation,
    note_evaluation_error,
    note_freshness,
)
from thytrader.execution.decisions import DecisionSkipReason
from thytrader.execution.entry_admission import _entry_verdict
from thytrader.execution.entry_sizing import (
    _await_maker_limit,
    _runtime_for_admitted_entry,
    _signal_intent_key,
    _size_entry_or_add,
)
from thytrader.execution.freshness import entry_prerequisites, signal_still_valid
from thytrader.execution.runtime_ops import _active_entry, _flatten_pending, _pause
from thytrader.execution.signals import evaluate_latest_entry_evidence, latest_atr
from thytrader.execution.submit import submit_intent
from thytrader.market_data.models import parse_candle_interval
from thytrader.risk.models import RiskDecision, RiskReasonCode, RiskVerdict, pauses_risk_increasing
from thytrader.strategies.models import can_pyramid_add
from thytrader.trading.geometry import EntrySkipReason, entry_order_side
from thytrader.trading.ids import utc_now
from thytrader.trading.lifecycle import entries_allowed
from thytrader.trading.models import (
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    IntentPurpose,
    OrderKind,
    OrderStatus,
    PositionSide,
    RuntimePhase,
    is_venue_protection,
    resolved_product_id,
    snapshot_positions,
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
