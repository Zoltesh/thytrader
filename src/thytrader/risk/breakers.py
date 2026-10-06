"""Daily-loss, drawdown, order-rate, and reference-price collar checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.ledger import ledger_from_snapshot
from thytrader.execution.models import (
    DeploymentKind,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    IntentPurpose,
    OrderStatus,
    resolved_product_id,
    snapshot_positions,
)
from thytrader.execution.performance import current_drawdown
from thytrader.market_data.products import is_spot_product_id, quote_currency
from thytrader.risk.exposure import daily_loss_snapshots, snapshot_has_residual_exposure
from thytrader.risk.models import RiskDecision, RiskPolicyDefinition, RiskReasonCode, RiskVerdict
from thytrader.risk.opening_accounting import reconstruct_day_open

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from uuid import UUID

    from thytrader.execution.models import OrderIntent

_OCCUPIED = {DeploymentStatus.RUNNING, DeploymentStatus.PAUSED}
_RATE_WINDOW = timedelta(seconds=60)


@dataclass(frozen=True, slots=True)
class EntryObservation:
    """Prices, marks, and wall-clock time used by breakers, rates, and collars."""

    as_of: datetime
    proposed_price: Decimal | None
    reference_price: Decimal | None
    marks: Mapping[str, Decimal]


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
    """Return a deny when same-quote daily-loss or matching-strategy drawdown trips.

    Daily loss and its latch include stopped flat books. Drawdown never spills across
    an unrelated strategy. Exposure and order-rate occupancy stay on ``_occupied_mode``.
    """
    quote = _product_quote(proposed_product_id)
    if quote is None:
        return _deny(
            RiskReasonCode.BREAKER_MARK_MISSING,
            "Daily-loss unavailable: proposed product quote is not a supported spot quote.",
        )
    daily_books, incomplete = _same_quote_books(
        daily_loss_snapshots(snapshots, mode), quote, purpose="Daily-loss"
    )
    if incomplete is not None:
        return incomplete
    latched = _daily_latch_verdict(daily_books)
    if latched is not None:
        return latched
    drawdown_latch = _drawdown_latch_verdict(
        snapshots,
        mode=mode,
        quote=quote,
        strategy_id=proposed_strategy_id,
        product_id=proposed_product_id,
    )
    if drawdown_latch is not None:
        return drawdown_latch
    daily = _daily_loss_verdict(
        policy, mode=mode, occupied=daily_books, observation=observation, capital=capital
    )
    drawdown = _drawdown_verdict(
        policy,
        snapshots=snapshots,
        mode=mode,
        quote=quote,
        proposed_strategy_id=proposed_strategy_id,
        proposed_product_id=proposed_product_id,
        observation=observation,
    )
    return _select_loss_verdict(daily, drawdown)


def _select_loss_verdict(
    daily: RiskVerdict | None, drawdown: RiskVerdict | None
) -> RiskVerdict | None:
    """Unknown daily evidence must not hide an independently proven local drawdown trip."""
    if daily is None:
        return drawdown
    if (
        daily.reason_code is RiskReasonCode.BREAKER_MARK_MISSING
        and drawdown is not None
        and drawdown.reason_code is RiskReasonCode.STRATEGY_DRAWDOWN_LIMIT
    ):
        return drawdown
    return daily


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
    return _collar_verdict(policy, observation=observation)


def breaker_pause_detail(reason_code: RiskReasonCode, detail: str) -> str:
    """Prefix a pause mismatch so operator findings keep a stable reason code."""
    return f"{reason_code.value}: {detail}"


def _daily_latch_verdict(books: Sequence[DeploymentSnapshot]) -> RiskVerdict | None:
    """Replay a same-quote daily-loss latch, including one left on a stopped flat book.

    Stop does not clear the flag. Only an explicit operator reset does.
    """
    if any(item.deployment.daily_loss_latched for item in books):
        return _deny(
            RiskReasonCode.DAILY_LOSS_LIMIT,
            "Daily-loss breaker is latched until an explicit operator reset.",
        )
    return None


def _drawdown_latch_verdict(
    snapshots: Sequence[DeploymentSnapshot],
    *,
    mode: DeploymentMode,
    quote: str,
    strategy_id: UUID | None,
    product_id: str,
) -> RiskVerdict | None:
    """Deny only when a matching strategy or discretionary product book is latched."""
    books, incomplete = _drawdown_books(
        snapshots, mode=mode, quote=quote, strategy_id=strategy_id, product_id=product_id
    )
    if incomplete is not None:
        return incomplete
    if any(item.deployment.drawdown_latched for item in books):
        return _deny(
            RiskReasonCode.STRATEGY_DRAWDOWN_LIMIT,
            "Drawdown breaker is latched for this strategy until an explicit operator reset.",
        )
    return None


def _daily_loss_verdict(
    policy: RiskPolicyDefinition,
    *,
    mode: DeploymentMode,
    occupied: Sequence[DeploymentSnapshot],
    observation: EntryObservation,
    capital: Decimal,
) -> RiskVerdict | None:
    """Trip when UTC-day equity change from day-open reaches the capital fraction."""
    loss = _mode_daily_loss(occupied, observation)
    if loss is None:
        return _deny(
            RiskReasonCode.BREAKER_MARK_MISSING,
            _daily_loss_missing_detail(occupied, observation),
        )
    if capital <= 0:
        return _deny(
            RiskReasonCode.BREAKER_MARK_MISSING,
            "Daily-loss unavailable: missing positive same-quote account capital.",
        )
    limit = capital * Decimal(policy.daily_loss_limit_fraction)
    # The absolute quote ceiling protects real money; paper uses the capital fraction only.
    if policy.max_daily_loss_quote is not None and mode is DeploymentMode.LIVE:
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
    snapshots: Sequence[DeploymentSnapshot],
    mode: DeploymentMode,
    quote: str,
    proposed_strategy_id: UUID | None,
    proposed_product_id: str,
    observation: EntryObservation,
) -> RiskVerdict | None:
    """Trip when a matching strategy book's pinned-capital drawdown reaches the cap."""
    books, incomplete = _drawdown_books(
        snapshots,
        mode=mode,
        quote=quote,
        strategy_id=proposed_strategy_id,
        product_id=proposed_product_id,
    )
    if incomplete is not None:
        return incomplete
    limit = Decimal(policy.max_strategy_drawdown_fraction)
    for target in books:
        verdict = _one_drawdown_verdict(target, observation=observation, limit=limit)
        if verdict is not None:
            return verdict
    return None


def _one_drawdown_verdict(
    target: DeploymentSnapshot, *, observation: EntryObservation, limit: Decimal
) -> RiskVerdict | None:
    """Evaluate one book's current drawdown without applying it to unrelated books."""
    if _open_inventory_missing_mark(target, observation.marks):
        return _deny(
            RiskReasonCode.BREAKER_MARK_MISSING,
            "Drawdown cannot be computed without a last-close mark on open inventory.",
        )
    ledger = ledger_from_snapshot(target, marks=observation.marks)
    if not ledger.mark_complete or ledger.equity is None:
        return _deny(
            RiskReasonCode.BREAKER_MARK_MISSING,
            "Drawdown cannot be computed without a last-close mark on open inventory.",
        )
    fraction = _durable_drawdown_fraction(target, equity=ledger.equity)
    if fraction is None:
        return _deny(
            RiskReasonCode.BREAKER_MARK_MISSING,
            f"Drawdown unavailable for deployment {target.deployment.id}: "
            "missing positive performance-capital basis.",
        )
    if fraction < limit:
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
    total = Decimal("0")
    for snapshot in occupied:
        if _open_inventory_missing_mark(snapshot, observation.marks):
            return None
        pnl = _daily_pnl(snapshot, marks=observation.marks, as_of=observation.as_of)
        if pnl is None:
            return None
        total += pnl
    if total >= 0:
        return Decimal("0")
    return -total


def _daily_pnl(
    snapshot: DeploymentSnapshot, *, marks: Mapping[str, Decimal], as_of: datetime
) -> Decimal | None:
    """Return this UTC day's equity change, never a previous day's stale baseline.

    Legacy UTC opening stamps lack provenance and are not trusted. Exact applied
    per-product fills reconstruct midnight cash/inventory; overnight inventory needs
    genuine closed midnight marks. Current marks and lifetime PnL cannot substitute.
    A focused product view is incomplete even when it contains no fills.
    """
    if not snapshot.accounting_complete or _unapplied_live_fills(snapshot):
        return None
    ledger = ledger_from_snapshot(snapshot, marks=marks)
    if not ledger.mark_complete or ledger.equity is None:
        return None
    day_start = _utc_day_start(as_of)
    if _utc_day_start(snapshot.deployment.created_at) == day_start:
        # Recorded deployment funding is genuine same-day opening evidence; a later
        # utc_day_open_* performance stamp is not. No legacy midnight stamp is used.
        return _pnl_from_opening_equity(snapshot, equity=ledger.equity)
    evidence = reconstruct_day_open(snapshot, as_of=as_of)
    if evidence is None:
        return None
    return ledger.equity - evidence.equity


def _pnl_from_opening_equity(snapshot: DeploymentSnapshot, *, equity: Decimal) -> Decimal | None:
    """Use the recorded opening balance for a book that started on this UTC day."""
    starting = snapshot.deployment.initial_equity
    if starting is None:
        starting = snapshot.deployment.paper_starting_cash
    if starting is None:
        return None
    return equity - starting


def _unapplied_live_fills(snapshot: DeploymentSnapshot) -> bool:
    """Unknown fill projection means live cash cannot establish complete loss evidence."""
    return snapshot.deployment.mode is DeploymentMode.LIVE and any(
        fill.economics_applied_at is None for fill in snapshot.fills
    )


def _daily_loss_missing_detail(
    occupied: Sequence[DeploymentSnapshot], observation: EntryObservation
) -> str:
    """Identify the book and missing evidence without inventing an equity baseline."""
    for snapshot in occupied:
        identity = f"deployment {snapshot.deployment.id}"
        if _unapplied_live_fills(snapshot):
            return f"Daily-loss unavailable for {identity}: unapplied live fill economics."
        if _open_inventory_missing_mark(snapshot, observation.marks):
            return f"Daily-loss unavailable for {identity}: missing last-close inventory marks."
        if _daily_pnl(snapshot, marks=observation.marks, as_of=observation.as_of) is None:
            if snapshot_positions(snapshot):
                return (
                    f"Daily-loss unavailable for {identity}: missing verified same-UTC-day "
                    "equity baseline or complete applied fill projections."
                )
            return (
                f"Daily-loss unavailable for {identity}: incomplete fill economics or missing "
                "same-UTC-day equity baseline (overnight inventory needs opening marks)."
            )
    return "Daily-loss unavailable: incomplete equity evidence."


def _drawdown_books(
    snapshots: Sequence[DeploymentSnapshot],
    *,
    mode: DeploymentMode,
    quote: str,
    strategy_id: UUID | None,
    product_id: str,
) -> tuple[tuple[DeploymentSnapshot, ...], RiskVerdict | None]:
    """Return same-quote books whose drawdown this entry would inherit."""
    candidates = [
        item
        for item in daily_loss_snapshots(snapshots, mode)
        if _matches_drawdown_scope(item, strategy_id=strategy_id, product_id=product_id)
    ]
    return _same_quote_books(candidates, quote, purpose="Drawdown")


def _matches_drawdown_scope(
    snapshot: DeploymentSnapshot, *, strategy_id: UUID | None, product_id: str
) -> bool:
    """Match a strategy's books, or a discretionary book on the proposed product."""
    deployment = snapshot.deployment
    if strategy_id is not None:
        return deployment.strategy_id == strategy_id
    if deployment.kind is not DeploymentKind.DISCRETIONARY:
        return False
    if deployment.product_id == product_id:
        return True
    return any(
        resolved_product_id(position.product_id, deployment) == product_id
        for position in snapshot_positions(snapshot)
    )


def quote_scoped_snapshots(
    snapshots: Sequence[DeploymentSnapshot], product_id: str
) -> tuple[tuple[DeploymentSnapshot, ...], RiskVerdict | None]:
    """Scope capital and exposure to the entry's quote, rejecting unreadable shared cash."""
    quote = _product_quote(product_id)
    if quote is None:
        return (), _deny(
            RiskReasonCode.BREAKER_MARK_MISSING,
            "Account risk unavailable: proposed product quote is not a supported spot quote.",
        )
    return _same_quote_books(snapshots, quote, purpose="Account risk")


def _same_quote_books(
    snapshots: Sequence[DeploymentSnapshot], quote: str, *, purpose: str
) -> tuple[tuple[DeploymentSnapshot, ...], RiskVerdict | None]:
    """Keep one spot quote. A mixed or unreadable book fails closed instead of summing."""
    selected: list[DeploymentSnapshot] = []
    for item in snapshots:
        book_quote = _snapshot_quote(item)
        if book_quote is None:
            return (), _deny(
                RiskReasonCode.BREAKER_MARK_MISSING,
                f"{purpose} unavailable for deployment {item.deployment.id}: "
                "unsupported or mixed quote currency.",
            )
        if book_quote == quote:
            selected.append(item)
    return tuple(selected), None


def _snapshot_quote(snapshot: DeploymentSnapshot) -> str | None:
    """Return the single spot quote on a book, or None when quotes cannot be summed."""
    quotes: set[str] = set()
    for product_id in _snapshot_products(snapshot):
        quote = _product_quote(product_id)
        if quote is None:
            return None
        quotes.add(quote)
    if len(quotes) != 1:
        return None
    return next(iter(quotes))


def _snapshot_products(snapshot: DeploymentSnapshot) -> tuple[str, ...]:
    """Products whose quote must agree before the book's PnL can enter a currency bucket."""
    deployment = snapshot.deployment
    products = {deployment.product_id}
    products.update(
        resolved_product_id(position.product_id, deployment)
        for position in snapshot_positions(snapshot)
    )
    products.update(runtime.product_id for runtime in snapshot.instrument_runtimes)
    products.update(resolved_product_id(order.product_id, deployment) for order in snapshot.orders)
    return tuple(product for product in products if product)


def _product_quote(product_id: str) -> str | None:
    """Return USD, USDC, or USDT, or None for an unsupported product id."""
    if not is_spot_product_id(product_id):
        return None
    return quote_currency(product_id)


def _durable_drawdown_fraction(snapshot: DeploymentSnapshot, *, equity: Decimal) -> Decimal | None:
    """Return peak-to-current drawdown using the persisted high-water mark."""
    return current_drawdown(snapshot.deployment, ledger_equity=equity)


def _occupied_mode(
    snapshots: Sequence[DeploymentSnapshot], mode: DeploymentMode
) -> tuple[DeploymentSnapshot, ...]:
    """Return exposure and rate-limit books, excluding stopped flat loss evidence."""
    occupied: list[DeploymentSnapshot] = []
    for item in snapshots:
        if item.deployment.mode is not mode:
            continue
        if item.deployment.status in _OCCUPIED:
            occupied.append(item)
            continue
        if item.deployment.status is DeploymentStatus.STOPPED and snapshot_has_residual_exposure(
            item
        ):
            occupied.append(item)
    return tuple(occupied)


def _utc_day_start(moment: datetime) -> datetime:
    """Return 00:00:00 UTC on the calendar day of ``moment``."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    as_utc = aware.astimezone(UTC)
    return datetime(as_utc.year, as_utc.month, as_utc.day, tzinfo=UTC)


def _open_inventory_missing_mark(
    snapshot: DeploymentSnapshot, marks: Mapping[str, Decimal]
) -> bool:
    """True when any open product book lacks a disclosed last-close mark."""
    for position in snapshot_positions(snapshot):
        product_id = resolved_product_id(position.product_id, snapshot.deployment)
        if marks.get(product_id) is None:
            return True
    return False


def _deny(reason_code: RiskReasonCode, detail: str) -> RiskVerdict:
    """Return a fail-closed breaker or collar verdict."""
    return RiskVerdict(decision=RiskDecision.DENY, reason_code=reason_code, detail=detail)
