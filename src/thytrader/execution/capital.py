"""Live capital accounts: venue quote, allocated capital, inventory, and equity."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.risk.opening_accounting import reconstruct_day_open, utc_day_start
from thytrader.trading.exposure import working_entry_notional
from thytrader.trading.ledger import ledger_from_snapshot
from thytrader.trading.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    snapshot_positions,
)
from thytrader.trading.performance import performance_capital

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from thytrader.trading.day_open import DailyOpeningEvidence

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
    observed = replace(
        deployment,
        venue_available_quote=available,
        allocated_capital=allocated,
        updated_at=now,
    )
    return replace(
        observed,
        performance_capital_quote=(
            deployment.performance_capital_quote
            if deployment.performance_capital_quote is not None
            else performance_capital(observed)
        ),
    )


def live_sizing_cash(deployment: Deployment) -> Decimal | None:
    """Quote available to size a live entry: allocated capital, else observed venue quote.

    Ledger ``cash`` is fill accounting and must not be replaced by venue available.
    ``None`` means the venue balance is unknown and entries must be denied. A paper
    sleeve of a deployed portfolio sizes from the smaller of its ledger cash and what is
    left of its allocated capital (weight times portfolio capital), so a rebalance binds
    paper sleeves the way it binds live ones; a paper sleeve never borrows cash it does
    not hold (ADR 0091).
    """
    if deployment.mode is DeploymentMode.PAPER:
        if deployment.portfolio_id is not None and deployment.allocated_capital is not None:
            return min(deployment.cash, _allocation_remaining(deployment))
        return deployment.cash
    if deployment.allocated_capital is not None:
        return _allocation_remaining(deployment)
    if deployment.venue_available_quote is None:
        return None
    return deployment.venue_available_quote


def _allocation_remaining(deployment: Deployment) -> Decimal:
    """Allocated capital minus working entries and open inventory cost (never negative)."""
    allocated = deployment.allocated_capital or _ZERO
    reserved = deployment.reserved_buying_power or _ZERO
    inventory = deployment.inventory_cost or _ZERO
    remaining = allocated - reserved - inventory
    return remaining if remaining > 0 else _ZERO


def live_capital_base(deployment: Deployment) -> Decimal | None:
    """Observe account quote for mode-wide risk, separately from this bot's allocation.

    The risk gate adds managed inventory and reserved entry quote exactly once. An
    allocation or ledger baseline cannot substitute for an unknown venue balance.
    """
    if deployment.mode is DeploymentMode.PAPER:
        return deployment.paper_starting_cash
    return deployment.venue_available_quote


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
    day_open_evidence = _roll_utc_day_open(snapshot, now=now)
    return replace(
        deployment,
        inventory_cost=inventory_cost,
        reserved_buying_power=reserved,
        performance_equity=equity,
        performance_capital_quote=(
            deployment.performance_capital_quote
            if deployment.performance_capital_quote is not None
            else performance_capital(deployment)
        ),
        performance_maximum_drawdown_fraction=(
            ledger.maximum_drawdown_fraction
            if ledger.maximum_drawdown_fraction is not None
            else deployment.performance_maximum_drawdown_fraction
        ),
        initial_equity=initial,
        baseline_equity=baseline,
        high_water_mark_equity=high_water,
        risk_day_open_evidence=day_open_evidence,
        updated_at=now,
    )


def daily_pnl_from_day_open(
    deployment: Deployment, *, equity: Decimal | None, as_of: datetime
) -> Decimal | None:
    """Return day change only for qualified evidence from the observed UTC day."""
    evidence = deployment.risk_day_open_evidence
    if equity is None or evidence is None or evidence.day_start != utc_day_start(as_of):
        return None
    return equity - evidence.equity


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
    snapshot: DeploymentSnapshot, *, now: datetime
) -> DailyOpeningEvidence | None:
    """Recover a proven opening without rewriting or promoting legacy equity stamps.

    Preserve prior derived evidence when recovery is incomplete. Readers revalidate
    it against current fills and the observation day, so preservation is not permission
    to reuse yesterday's baseline or to hide a late fill.
    """
    recovered = reconstruct_day_open(snapshot, as_of=now)
    return recovered if recovered is not None else snapshot.deployment.risk_day_open_evidence
