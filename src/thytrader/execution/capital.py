"""Live capital accounts: venue quote, allocated capital, inventory, and equity."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.execution.ledger import ledger_from_snapshot
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    Position,
    snapshot_positions,
)
from thytrader.risk.exposure import working_entry_notional

if TYPE_CHECKING:
    from collections.abc import Mapping

_ZERO = Decimal("0")


def apply_venue_quote(
    deployment: Deployment, *, available: Decimal | None, now: datetime
) -> Deployment:
    """Stamp observed venue quote without overwriting strategy ledger cash.

    Unknown balances clear ``venue_available_quote`` so callers can disable
    entries rather than keep a stale cash figure.
    """
    allocated = deployment.allocated_capital
    if allocated is None and deployment.mode is DeploymentMode.PAPER:
        allocated = deployment.paper_starting_cash
    return replace(
        deployment,
        venue_available_quote=available,
        allocated_capital=allocated,
        updated_at=now,
    )


def live_sizing_cash(deployment: Deployment) -> Decimal | None:
    """Quote available to size a live entry: allocated capital, else observed venue quote.

    Ledger ``cash`` is fill accounting and must not be replaced by venue available.
    ``None`` means the venue balance is unknown and entries must be denied.
    """
    if deployment.mode is DeploymentMode.PAPER:
        return deployment.cash
    if deployment.allocated_capital is not None:
        reserved = deployment.reserved_buying_power or _ZERO
        inventory = deployment.inventory_cost or _ZERO
        remaining = deployment.allocated_capital - reserved - inventory
        return remaining if remaining > 0 else _ZERO
    if deployment.venue_available_quote is None:
        return None
    return deployment.venue_available_quote


def live_capital_base(deployment: Deployment) -> Decimal | None:
    """Portfolio capital used by live exposure and breaker fractions."""
    if deployment.mode is DeploymentMode.PAPER:
        return deployment.paper_starting_cash
    if deployment.allocated_capital is not None and deployment.allocated_capital > 0:
        return deployment.allocated_capital
    if deployment.initial_equity is not None and deployment.initial_equity > 0:
        return deployment.initial_equity
    if deployment.venue_available_quote is not None and deployment.venue_available_quote > 0:
        return deployment.venue_available_quote
    return None


def refresh_performance(
    snapshot: DeploymentSnapshot,
    *,
    marks: Mapping[str, Decimal] | None = None,
    mark_price: Decimal | None = None,
    now: datetime,
) -> Deployment:
    """Persist inventory cost, performance equity, HWM, and UTC day-open baseline."""
    deployment = snapshot.deployment
    inventory_cost = _inventory_cost(snapshot)
    reserved = _reserved_working(snapshot)
    ledger = ledger_from_snapshot(snapshot, marks=marks, mark_price=mark_price)
    equity = ledger.equity
    initial = deployment.initial_equity
    baseline = deployment.baseline_equity
    if initial is None and equity is not None:
        initial = equity
    if baseline is None:
        baseline = initial
    high_water = deployment.high_water_mark_equity
    if equity is not None:
        high_water = equity if high_water is None else max(high_water, equity)
    day_open, day_at = _roll_utc_day_open(deployment, equity=equity, now=now)
    return replace(
        deployment,
        inventory_cost=inventory_cost,
        reserved_buying_power=reserved,
        performance_equity=equity,
        initial_equity=initial,
        baseline_equity=baseline,
        high_water_mark_equity=high_water,
        utc_day_open_equity=day_open,
        utc_day_open_at=day_at,
        updated_at=now,
    )


def daily_pnl_from_day_open(deployment: Deployment, *, equity: Decimal | None) -> Decimal | None:
    """Return UTC-day equity change from the persisted day-open baseline."""
    if equity is None or deployment.utc_day_open_equity is None:
        return None
    return equity - deployment.utc_day_open_equity


def _inventory_cost(snapshot: DeploymentSnapshot) -> Decimal:
    """Sum entry-price cost of every open product book."""
    total = _ZERO
    for position in snapshot_positions(snapshot):
        total += position.quantity * position.entry_price
    return total


def _reserved_working(snapshot: DeploymentSnapshot) -> Decimal:
    """Sum remaining quote on active non-bracket orders."""
    products = {snapshot.deployment.product_id}
    for position in snapshot_positions(snapshot):
        products.add(position.product_id or snapshot.deployment.product_id)
    total = _ZERO
    for product_id in products:
        total += working_entry_notional(snapshot, product_id)
    return total


def _roll_utc_day_open(
    deployment: Deployment, *, equity: Decimal | None, now: datetime
) -> tuple[Decimal | None, datetime | None]:
    """Keep day-open equity until UTC midnight, then snapshot the new day's open."""
    aware = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    as_utc = aware.astimezone(UTC)
    day_start = datetime(as_utc.year, as_utc.month, as_utc.day, tzinfo=UTC)
    if deployment.utc_day_open_at is None or deployment.utc_day_open_at < day_start:
        return equity, day_start
    return deployment.utc_day_open_equity, deployment.utc_day_open_at


def marked_inventory_value(position: Position | None, mark: Decimal | None) -> Decimal | None:
    """Return marked inventory value, or None when a required mark is missing."""
    if position is None:
        return _ZERO
    if mark is None:
        return None
    return position.quantity * mark
