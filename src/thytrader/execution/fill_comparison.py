"""Read-only paper vs live entry-fill comparison for twin deployments (ADR 0097).

A paper book fills its resting post-only entry only when a later closed candle trades
through the limit; the live twin fills whenever Coinbase matches it, often within
seconds. Both run the same rules when they bind the same content-addressed strategy
snapshot (``strategy_fingerprint``, ADR 0082), so a paper and a live strategy book with
equal fingerprints are twins. This module measures, per mode, how many entries rested,
filled, expired, or were rejected, the average fill against the posted limit, and the
time from rest to first fill. It never touches orders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from statistics import median
from typing import TYPE_CHECKING

from thytrader.execution.models import (
    DeploymentKind,
    DeploymentMode,
    IntentPurpose,
    OrderSide,
    OrderStatus,
)
from thytrader.market_data.models import parse_candle_interval

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from thytrader.execution.models import Deployment, DeploymentSnapshot, Fill, Order

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
    """One paper and one live strategy book bound to the same strategy snapshot."""

    strategy_fingerprint: str
    strategy_id: UUID | None
    strategy_name: str | None
    product_id: str
    paper_deployment_id: UUID
    live_deployment_id: UUID


def paper_live_twins(deployments: Sequence[Deployment], *, limit: int) -> tuple[PaperLiveTwin, ...]:
    """Pair the newest paper and newest live strategy book per strategy fingerprint.

    Only strategy (not discretionary) books with a fingerprint pair. Pairs are ordered by
    the newer twin's creation time, newest first, and bounded by ``limit``.
    """
    newest: dict[tuple[str, DeploymentMode], Deployment] = {}
    for item in deployments:
        if item.kind is not DeploymentKind.STRATEGY or not item.strategy_fingerprint:
            continue
        key = (item.strategy_fingerprint, item.mode)
        current = newest.get(key)
        if current is None or item.created_at > current.created_at:
            newest[key] = item
    pairs: list[tuple[Deployment, Deployment]] = []
    for (fingerprint, mode), paper in newest.items():
        if mode is not DeploymentMode.PAPER:
            continue
        live = newest.get((fingerprint, DeploymentMode.LIVE))
        if live is not None:
            pairs.append((paper, live))
    pairs.sort(key=lambda pair: max(pair[0].created_at, pair[1].created_at), reverse=True)
    return tuple(
        PaperLiveTwin(
            strategy_fingerprint=paper.strategy_fingerprint or "",
            strategy_id=live.strategy_id or paper.strategy_id,
            strategy_name=live.strategy_name or paper.strategy_name,
            product_id=live.product_id,
            paper_deployment_id=paper.id,
            live_deployment_id=live.id,
        )
        for paper, live in pairs[:limit]
    )


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
