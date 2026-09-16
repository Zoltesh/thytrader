"""Daily-loss, drawdown, order-rate, and reference-price collar checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.ledger import ledger_from_snapshot, realized_pnl_since
from thytrader.execution.models import (
    DeploymentMode,
    IntentPurpose,
    OrderStatus,
)
from thytrader.market_data.freshness import FreshnessStatus, evaluate_freshness
from thytrader.market_data.models import CandleInterval
from thytrader.risk.exposure import risk_bearing_snapshots
from thytrader.risk.models import RiskDecision, RiskPolicyDefinition, RiskReasonCode, RiskVerdict

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from uuid import UUID

    from thytrader.execution.models import DeploymentSnapshot, OrderIntent

_RATE_WINDOW = timedelta(seconds=60)
_REFERENCE_FRESHNESS_GRACE_SECONDS = 300


@dataclass(frozen=True, slots=True)
class EntryObservation:
    """Prices, marks, and wall-clock time used by breakers, rates, and collars."""

    as_of: datetime
    proposed_price: Decimal | None
    reference_price: Decimal | None
    marks: Mapping[str, Decimal]
    reference_candle_at: datetime | None = None
    reference_interval_seconds: int | None = None


def evaluate_circuit_breakers(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    proposed_product_id: str,
    proposed_strategy_id: UUID | None,
    snapshots: Sequence[DeploymentSnapshot],
    observation: EntryObservation,
    capital: Decimal,
) -> RiskVerdict | None:
    """Return a deny when daily-loss or per-strategy drawdown is at the limit."""
    occupied = _occupied_mode(snapshots, mode)
    daily = _daily_loss_verdict(policy, occupied=occupied, observation=observation, capital=capital)
    if daily is not None:
        return daily
    return _drawdown_verdict(
        policy,
        occupied=occupied,
        proposed_strategy_id=proposed_strategy_id,
        proposed_product_id=proposed_product_id,
        observation=observation,
    )


def evaluate_rate_and_collar(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    snapshots: Sequence[DeploymentSnapshot],
    observation: EntryObservation,
) -> RiskVerdict | None:
    """Return a deny when order/cancel rates or the price collar are breached."""
    occupied = _occupied_mode(snapshots, mode)
    rate = _rate_verdict(policy, occupied=occupied, as_of=observation.as_of)
    if rate is not None:
        return rate
    stale = _stale_reference_verdict(observation=observation)
    if stale is not None:
        return stale
    return _collar_verdict(policy, observation=observation)


def breaker_pause_detail(reason_code: RiskReasonCode, detail: str) -> str:
    """Prefix a pause mismatch so operator findings keep a stable reason code."""
    return f"{reason_code.value}: {detail}"


def _daily_loss_verdict(
    policy: RiskPolicyDefinition,
    *,
    occupied: Sequence[DeploymentSnapshot],
    observation: EntryObservation,
    capital: Decimal,
) -> RiskVerdict | None:
    """Trip when UTC-day realized plus unrealized loss reaches the capital fraction."""
    if capital <= 0:
        return None
    loss = _mode_daily_loss(occupied, observation)
    if loss is None:
        return _deny(
            RiskReasonCode.BREAKER_MARK_MISSING,
            "Daily-loss cannot be computed without a last-close mark on open inventory.",
        )
    limit = capital * Decimal(policy.daily_loss_limit_fraction)
    if policy.max_daily_loss_quote is not None:
        limit = min(limit, Decimal(policy.max_daily_loss_quote))
    if loss < limit:
        return None
    return _deny(
        RiskReasonCode.DAILY_LOSS_LIMIT,
        "Daily realized plus unrealized loss reached the risk-policy limit.",
    )


def _drawdown_verdict(
    policy: RiskPolicyDefinition,
    *,
    occupied: Sequence[DeploymentSnapshot],
    proposed_strategy_id: UUID | None,
    proposed_product_id: str,
    observation: EntryObservation,
) -> RiskVerdict | None:
    """Trip when this strategy's fill-ledger drawdown reaches the policy fraction."""
    target = _drawdown_target(
        occupied,
        strategy_id=proposed_strategy_id,
        product_id=proposed_product_id,
    )
    if target is None:
        return None
    mark = observation.marks.get(target.deployment.product_id)
    if target.position is not None and mark is None:
        return _deny(
            RiskReasonCode.BREAKER_MARK_MISSING,
            "Drawdown cannot be computed without a last-close mark on open inventory.",
        )
    ledger = ledger_from_snapshot(target, mark_price=mark)
    fraction = ledger.maximum_drawdown_fraction
    if fraction is None:
        fraction = Decimal("0")
    if fraction < Decimal(policy.max_strategy_drawdown_fraction):
        return None
    return _deny(
        RiskReasonCode.STRATEGY_DRAWDOWN_LIMIT,
        "Per-strategy fill-ledger drawdown reached the risk-policy limit.",
    )


def _rate_verdict(
    policy: RiskPolicyDefinition,
    *,
    occupied: Sequence[DeploymentSnapshot],
    as_of: datetime,
) -> RiskVerdict | None:
    """Deny new entries when the rolling-minute entry-order, cancel, or venue budget is exhausted.

    Only ENTRY-purpose orders consume ``max_entry_orders_per_minute`` (audit F35):
    protective (stop/take-profit/time-exit/bracket) submissions must not silently
    exhaust the entry budget and block an unrelated new entry. This function is only
    ever called while admitting a new risk-increasing entry (see risk/gate.py); it
    never gates a cancellation or protective submission itself, so risk-reducing work
    keeps flowing even while an exhausted cap denies a new entry.
    """
    since = as_of - _RATE_WINDOW
    entry_orders = 0
    cancels = 0
    venue_actions = 0
    for snapshot in occupied:
        entry_intent_ids = {
            intent.id for intent in snapshot.intents if intent.purpose is IntentPurpose.ENTRY
        }
        for order in snapshot.orders:
            if order.created_at >= since:
                venue_actions += 1
                if _is_entry_purpose(order.intent_id, entry_intent_ids, snapshot.intents):
                    entry_orders += 1
            if order.status is OrderStatus.CANCELED and order.updated_at >= since:
                cancels += 1
                venue_actions += 1
    if entry_orders >= policy.max_entry_orders_per_minute:
        return _deny(
            RiskReasonCode.ORDER_RATE_LIMIT,
            "Entry orders in the last minute reached the risk-policy rate limit.",
        )
    if cancels >= policy.max_cancellations_per_minute:
        return _deny(
            RiskReasonCode.CANCEL_RATE_LIMIT,
            "Cancellations in the last minute reached the risk-policy rate limit.",
        )
    if (
        policy.max_venue_order_actions_per_minute is not None
        and venue_actions >= policy.max_venue_order_actions_per_minute
    ):
        return _deny(
            RiskReasonCode.VENUE_REQUEST_BUDGET_EXCEEDED,
            "Combined entry, cancel, and replacement requests in the last minute "
            "reached the risk-policy venue budget.",
        )
    return None


def _is_entry_purpose(
    intent_id: UUID,
    entry_intent_ids: set[UUID],
    intents: Sequence[OrderIntent],
) -> bool:
    """Return whether one order's intent purpose is ENTRY.

    Prefers the immutable intent record; when no snapshot intents are available at
    all (for example an ad-hoc snapshot without a durable store), falls back to
    treating every order as a candidate entry so a degraded snapshot fails closed on
    the rate cap rather than silently exempting unknown orders from it.
    """
    if intents:
        return intent_id in entry_intent_ids
    return True


def _stale_reference_verdict(*, observation: EntryObservation) -> RiskVerdict | None:
    """Deny when the reference candle is stale relative to the strategy interval."""
    candle_at = observation.reference_candle_at
    interval_seconds = observation.reference_interval_seconds
    if candle_at is None or interval_seconds is None:
        return None
    interval = _candle_interval_from_seconds(interval_seconds)
    if interval is not None:
        freshness = evaluate_freshness(
            product_id="reference",
            newest_candle_at=candle_at,
            now=observation.as_of,
            interval=interval,
        )
        if freshness.status is FreshnessStatus.FRESH:
            return None
        return _deny(
            RiskReasonCode.REFERENCE_PRICE_UNAVAILABLE,
            "Reference candle is stale or missing; a fresh last-close mark is required.",
        )
    threshold = 2 * interval_seconds + _REFERENCE_FRESHNESS_GRACE_SECONDS
    age = int((observation.as_of - candle_at).total_seconds())
    if 0 <= age < threshold:
        return None
    return _deny(
        RiskReasonCode.REFERENCE_PRICE_UNAVAILABLE,
        "Reference candle is stale or missing; a fresh last-close mark is required.",
    )


def _candle_interval_from_seconds(interval_seconds: int) -> CandleInterval | None:
    """Map a strategy interval length to a known candle interval, if one exists."""
    for candidate in CandleInterval:
        if int(candidate.duration.total_seconds()) == interval_seconds:
            return candidate
    return None


def _collar_verdict(
    policy: RiskPolicyDefinition, *, observation: EntryObservation
) -> RiskVerdict | None:
    """Deny a priced entry that is farther from last close than the collar allows."""
    reference = observation.reference_price
    if reference is None or reference <= 0:
        return _deny(
            RiskReasonCode.REFERENCE_PRICE_UNAVAILABLE,
            "A positive last-close reference price is required before a risk-increasing order.",
        )
    proposed = observation.proposed_price
    if proposed is None:
        return None
    if proposed <= 0:
        return _deny(
            RiskReasonCode.REFERENCE_PRICE_COLLAR,
            "Proposed entry price must be positive under the reference-price collar.",
        )
    deviation = abs(proposed - reference) / reference
    if deviation <= Decimal(policy.reference_price_collar_fraction):
        return None
    return _deny(
        RiskReasonCode.REFERENCE_PRICE_COLLAR,
        "Proposed entry price exceeds the reference-price collar versus last close.",
    )


def _mode_daily_loss(
    occupied: Sequence[DeploymentSnapshot], observation: EntryObservation
) -> Decimal | None:
    """Sum UTC-day losses across occupied books, or None when a required mark is missing."""
    day_start = _utc_day_start(observation.as_of)
    total = Decimal("0")
    for snapshot in occupied:
        mark = observation.marks.get(snapshot.deployment.product_id)
        pnl = _daily_pnl(snapshot, mark=mark, day_start=day_start)
        if pnl is None:
            return None
        total += pnl
    if total >= 0:
        return Decimal("0")
    return -total


def _daily_pnl(
    snapshot: DeploymentSnapshot, *, mark: Decimal | None, day_start: datetime
) -> Decimal | None:
    """Return this book's UTC-day realized plus current unrealized PnL."""
    ledger = ledger_from_snapshot(snapshot, mark_price=mark)
    if not ledger.mark_complete:
        return None
    realized_today = realized_pnl_since(snapshot, since=day_start)
    unrealized = ledger.unrealized_net_pnl
    if unrealized is None:
        unrealized = Decimal("0")
    return realized_today + unrealized


def _drawdown_target(
    occupied: Sequence[DeploymentSnapshot],
    *,
    strategy_id: UUID | None,
    product_id: str,
) -> DeploymentSnapshot | None:
    """Pick the occupied book whose drawdown this proposed entry would inherit."""
    if strategy_id is not None:
        match = next(
            (item for item in occupied if item.deployment.strategy_id == strategy_id), None
        )
        if match is not None:
            return match
    return next((item for item in occupied if item.deployment.product_id == product_id), None)


def _occupied_mode(
    snapshots: Sequence[DeploymentSnapshot], mode: DeploymentMode
) -> tuple[DeploymentSnapshot, ...]:
    """Return risk-bearing snapshots in one paper or live mode."""
    return risk_bearing_snapshots(snapshots, mode)


def _utc_day_start(moment: datetime) -> datetime:
    """Return 00:00:00 UTC on the calendar day of ``moment``."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    as_utc = aware.astimezone(UTC)
    return datetime(as_utc.year, as_utc.month, as_utc.day, tzinfo=UTC)


def _deny(reason_code: RiskReasonCode, detail: str) -> RiskVerdict:
    """Return a fail-closed breaker or collar verdict."""
    return RiskVerdict(decision=RiskDecision.DENY, reason_code=reason_code, detail=detail)
