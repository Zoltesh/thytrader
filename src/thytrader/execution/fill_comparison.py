"""Read-only paper vs live entry-fill comparison for twin deployments (ADR 0097).

A paper book fills its resting post-only entry only when a later closed candle trades
through the limit; the live twin fills whenever Coinbase matches it, often within
seconds. Both run the same rules when they bind the same content-addressed strategy
snapshot (``strategy_fingerprint``, ADR 0082). Operators explicitly select the pair
(ADR 0102); shared fingerprints alone never select a partner. This module measures
entry outcomes without touching orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from statistics import median
from typing import TYPE_CHECKING

from thytrader.market_data.models import parse_candle_interval
from thytrader.trading.models import (
    DeploymentMode,
    IntentPurpose,
    OrderSide,
    OrderStatus,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from thytrader.trading.models import Deployment, DeploymentSnapshot, Fill, Order
    from thytrader.trading.twins import DeploymentTwinLink

_BPS = Decimal(10000)
_ACTIVE = frozenset({OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.UNKNOWN})
_ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class EntryFillStats:
    """Entry-order outcomes for one deployment.

    ``average_fill_vs_limit_bps`` is signed so that positive is worse than the posted
    limit (a buy above it, a sell below it); a maker fill at the limit is zero.
    ``*_seconds_to_fill`` measure from the entry order's creation to its first known fill
    (paper: the fill bar's close, when the worker can first see it; live: the venue fill).
    """

    deployment_id: UUID
    mode: DeploymentMode
    status: str
    entries_rested: int
    entries_filled: int
    entries_expired: int
    entries_rejected: int
    entries_working: int
    average_fill_vs_limit_bps: Decimal | None
    average_seconds_to_fill: Decimal | None
    median_seconds_to_fill: Decimal | None


@dataclass(frozen=True, slots=True)
class PaperLiveTwin:
    """Explicit rule-matched twins, with the paper fingerprint as compatibility identity."""

    strategy_fingerprint: str
    strategy_id: UUID | None
    strategy_name: str | None
    product_id: str
    paper_deployment_id: UUID
    live_deployment_id: UUID


def paper_live_twins(
    deployments: Sequence[Deployment], links: Sequence[DeploymentTwinLink], *, limit: int
) -> tuple[PaperLiveTwin, ...]:
    """Resolve explicit pairs in newest-linked order; never infer missing partners."""
    by_id = {item.id: item for item in deployments}
    pairs: list[PaperLiveTwin] = []
    for link in sorted(
        links, key=lambda item: (item.linked_at, item.paper_deployment_id), reverse=True
    ):
        paper = by_id.get(link.paper_deployment_id)
        live = by_id.get(link.live_deployment_id)
        if paper is None or live is None:
            continue
        pairs.append(
            PaperLiveTwin(
                strategy_fingerprint=paper.strategy_fingerprint or "",
                strategy_id=live.strategy_id or paper.strategy_id,
                strategy_name=live.strategy_name or paper.strategy_name,
                product_id=live.product_id,
                paper_deployment_id=paper.id,
                live_deployment_id=live.id,
            )
        )
    return tuple(pairs[:limit])


def entry_fill_stats(snapshot: DeploymentSnapshot) -> EntryFillStats:
    """Classify every entry order of one full snapshot (orders, intents, and fills).

    An entry order is one whose intent purpose is ``entry`` (first entries and pyramid
    adds; each reprice is its own order). Filled means any fill quantity; expired means
    canceled with nothing filled (the wait ran out, a reprice replaced it, or the book
    stopped); rejected includes post-only crosses; working is still resting.
    """
    entry_intents = {
        intent.id for intent in snapshot.intents if intent.purpose is IntentPurpose.ENTRY
    }
    entries = tuple(order for order in snapshot.orders if order.intent_id in entry_intents)
    fills_by_order: dict[UUID, list[Fill]] = {}
    for fill in snapshot.fills:
        fills_by_order.setdefault(fill.order_id, []).append(fill)
    filled = tuple(order for order in entries if fills_by_order.get(order.id))
    unfilled = tuple(order for order in entries if not fills_by_order.get(order.id))
    adverse = [
        value
        for order in filled
        if (value := _adverse_bps(order, fills_by_order[order.id])) is not None
    ]
    deployment = snapshot.deployment
    recognized_after = _paper_recognition_lag(deployment)
    waits = [
        _seconds_to_fill(order, fills_by_order[order.id], recognized_after=recognized_after)
        for order in filled
    ]
    return EntryFillStats(
        deployment_id=deployment.id,
        mode=deployment.mode,
        status=deployment.status.value,
        entries_rested=len(entries),
        entries_filled=len(filled),
        entries_expired=sum(1 for order in unfilled if order.status is OrderStatus.CANCELED),
        entries_rejected=sum(1 for order in unfilled if order.status is OrderStatus.REJECTED),
        entries_working=sum(1 for order in unfilled if order.status in _ACTIVE),
        average_fill_vs_limit_bps=_mean(adverse),
        average_seconds_to_fill=_mean(waits),
        median_seconds_to_fill=None if not waits else Decimal(median(waits)),
    )


def _adverse_bps(order: Order, fills: Sequence[Fill]) -> Decimal | None:
    """Quantity-weighted fill price against the limit, positive when worse, in bps."""
    quantity = sum((fill.quantity for fill in fills), _ZERO)
    if order.price is None or order.price <= 0 or quantity <= 0:
        return None
    average = sum((fill.price * fill.quantity for fill in fills), _ZERO) / quantity
    difference = average - order.price if order.side is OrderSide.BUY else order.price - average
    return difference / order.price * _BPS


def _paper_recognition_lag(deployment: Deployment) -> timedelta:
    """How long after its ``filled_at`` a fill becomes known to the runtime.

    A paper fill is stamped with its fill bar's start but only exists once that bar has
    closed, so paper waits run to the bar close; live fills are stamped by the venue.
    """
    if deployment.mode is not DeploymentMode.PAPER or not deployment.timeframe:
        return timedelta(0)
    try:
        return parse_candle_interval(deployment.timeframe).duration
    except ValueError:
        return timedelta(0)


def _seconds_to_fill(
    order: Order, fills: Sequence[Fill], *, recognized_after: timedelta
) -> Decimal:
    """Seconds from the entry order's creation to its first known fill (never negative)."""
    first = min(fill.filled_at for fill in fills) + recognized_after
    return max(_ZERO, Decimal(str((first - order.created_at).total_seconds())))


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    """Arithmetic mean, or None for no values."""
    if not values:
        return None
    return sum(values, _ZERO) / len(values)
