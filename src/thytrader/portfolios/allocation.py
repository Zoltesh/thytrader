"""Portfolio allocation arithmetic and the percent and quote text it is rendered with.

Sleeve weights plus the cash reserve never exceed 1, checked with exact decimal arithmetic.
The allocation summary splits capital between sleeves, the reserve, and unallocated cash,
and groups weight by base asset (a multi-product sleeve counts toward each asset).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.decimal_text import canonical_decimal
from thytrader.portfolios.errors import PortfolioValidationError
from thytrader.portfolios.models import PortfolioAggregate, SleeveView, asset_of

if TYPE_CHECKING:
    from collections.abc import Iterable
    from uuid import UUID


_ONE = Decimal(1)
_HUNDRED = Decimal(100)


@dataclass(frozen=True, slots=True)
class AssetAllocation:
    """Weight held in one base asset (a multi-product sleeve counts toward each asset)."""

    asset: str
    weight_fraction: str
    sleeve_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class AllocationSummary:
    """How capital splits between sleeves, the cash reserve, and unallocated cash."""

    allocated_fraction: str
    cash_reserve_fraction: str
    unallocated_fraction: str
    allocated_quote: str
    cash_reserve_quote: str
    unallocated_quote: str
    assets: tuple[AssetAllocation, ...]
    largest_asset: AssetAllocation | None
    largest_asset_within_limit: bool | None


def percent_text(fraction: str | Decimal) -> str:
    """Render a fraction as a percent without trailing zeros (``0.3333`` → ``33.33%``)."""
    return f"{canonical_decimal(Decimal(fraction) * _HUNDRED)}%"


def quote_text(amount: str | Decimal, currency: str) -> str:
    """Render a quote amount with grouped digits and its currency."""
    text = canonical_decimal(Decimal(amount))
    whole, separator, fraction = text.partition(".")
    sign = "-" if whole.startswith("-") else ""
    digits = whole.removeprefix("-")
    grouped = f"{int(digits):,}"
    return f"{sign}{grouped}{separator}{fraction} {currency}"


def sleeve_capital(capital_quote: str, weight_fraction: str) -> str:
    """Return the exact quote capital one weight represents."""
    return canonical_decimal(Decimal(capital_quote) * Decimal(weight_fraction))


def allocated_fraction(weights: Iterable[str]) -> Decimal:
    """Exact sum of sleeve weights."""
    return sum((Decimal(weight) for weight in weights), start=Decimal(0))


def require_allocation(weights: Iterable[str], cash_reserve_fraction: str) -> None:
    """Refuse weights plus the cash reserve above 100% of capital."""
    allocated = allocated_fraction(weights)
    reserve = Decimal(cash_reserve_fraction)
    total = allocated + reserve
    if total > _ONE:
        raise PortfolioValidationError(
            "portfolio_allocation_exceeded",
            f"Sleeve weights ({percent_text(allocated)}) plus the cash reserve "
            f"({percent_text(reserve)}) come to {percent_text(total)}; together they must "
            "not exceed 100%.",
        )


def allocation_summary(aggregate: PortfolioAggregate) -> AllocationSummary:
    """Summarize allocation by sleeve, reserve, unallocated cash, and base asset."""
    portfolio = aggregate.portfolio
    weights = [view.sleeve.weight_fraction for view in aggregate.sleeves]
    allocated = allocated_fraction(weights)
    reserve = Decimal(portfolio.cash_reserve_fraction)
    unallocated = max(Decimal(0), _ONE - allocated - reserve)
    capital = Decimal(portfolio.capital_quote)
    assets = _asset_allocations(aggregate.sleeves)
    largest = assets[0] if assets else None
    within: bool | None = None
    if largest is not None:
        within = Decimal(largest.weight_fraction) <= Decimal(
            portfolio.limits.max_per_asset_fraction
        )
    return AllocationSummary(
        allocated_fraction=canonical_decimal(allocated),
        cash_reserve_fraction=canonical_decimal(reserve),
        unallocated_fraction=canonical_decimal(unallocated),
        allocated_quote=canonical_decimal(capital * allocated),
        cash_reserve_quote=canonical_decimal(capital * reserve),
        unallocated_quote=canonical_decimal(capital * unallocated),
        assets=assets,
        largest_asset=largest,
        largest_asset_within_limit=within,
    )


def _asset_allocations(sleeves: tuple[SleeveView, ...]) -> tuple[AssetAllocation, ...]:
    """Group sleeve weights by base asset, largest first (ties by asset name)."""
    weights: dict[str, Decimal] = {}
    members: dict[str, list[UUID]] = {}
    for view in sleeves:
        assets = dict.fromkeys(asset_of(product) for product in view.strategy.covered_product_ids)
        for asset in assets:
            weights[asset] = weights.get(asset, Decimal(0)) + Decimal(view.sleeve.weight_fraction)
            members.setdefault(asset, []).append(view.sleeve.sleeve_id)
    ordered = sorted(weights, key=lambda asset: (-weights[asset], asset))
    return tuple(
        AssetAllocation(
            asset=asset,
            weight_fraction=canonical_decimal(weights[asset]),
            sleeve_ids=tuple(members[asset]),
        )
        for asset in ordered
    )
