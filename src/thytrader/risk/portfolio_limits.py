"""A deployed portfolio's shared limits as the entry gate applies them (ADR 0091).

A sleeve's entry is refused while its portfolio's limits are unavailable or its breaker is
latched, and when it would take the portfolio's total or per-asset exposure above the caps
(fractions of the portfolio's capital).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.market_data.products import base_currency, is_spot_product_id
from thytrader.risk.gate_common import (
    ProposedEntry,
    _allow,
    _book_products,
    _deny,
    _marked_exposure,
)
from thytrader.risk.models import RiskReasonCode, RiskVerdict
from thytrader.trading.exposure import product_exposure

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from uuid import UUID

    from thytrader.trading.models import DeploymentSnapshot


@dataclass(frozen=True, slots=True)
class PortfolioRiskBook:
    """One deployed portfolio's shared limits as the entry gate applies them (ADR 0091).

    Bound by the execution worker for every deployment tagged with a ``portfolio_id``.
    Exposure caps are fractions of the portfolio's ``capital`` (its configured
    ``capital_quote``) and count every risk-bearing deployment of the same portfolio.
    ``breaker_reason`` names a latched portfolio breaker (entries stay blocked until an
    operator reset). ``available=False`` means the worker could not load the portfolio's
    limits, so its sleeves fail closed. ``live`` marks a live portfolio, whose sleeve
    allocations count as risk-policy allocation membership for its own deployments.
    """

    portfolio_id: UUID
    capital: Decimal
    max_total_exposure_fraction: Decimal
    max_per_asset_fraction: Decimal
    live: bool = False
    breaker_reason: RiskReasonCode | None = None
    available: bool = True

    @classmethod
    def unavailable(cls, portfolio_id: UUID) -> PortfolioRiskBook:
        """A fail-closed book for a sleeve whose portfolio limits could not be read."""
        return cls(
            portfolio_id=portfolio_id,
            capital=Decimal("0"),
            max_total_exposure_fraction=Decimal("0"),
            max_per_asset_fraction=Decimal("0"),
            available=False,
        )


def evaluate_portfolio_entry(
    book: PortfolioRiskBook,
    *,
    proposed: ProposedEntry,
    snapshots: Sequence[DeploymentSnapshot],
) -> RiskVerdict:
    """Apply one portfolio's latched breaker and exposure caps to a sleeve's entry.

    ``snapshots`` are the mode's risk-bearing books; only deployments tagged with this
    portfolio count. Total exposure is every sleeve's position value at entry price plus
    working entry remainders; per-asset exposure sums every product of the proposed
    base asset.
    """
    if not book.available:
        return _deny(
            RiskReasonCode.PORTFOLIO_LIMITS_UNAVAILABLE,
            "Portfolio limits could not be loaded; new entries for its sleeves are blocked.",
        )
    if book.breaker_reason is not None:
        return _deny(
            RiskReasonCode.PORTFOLIO_BREAKER_LATCHED,
            f"Portfolio breaker {book.breaker_reason.value} is latched until an operator "
            "resets it.",
        )
    exposure = portfolio_exposure(book.portfolio_id, snapshots)
    total_cap = book.capital * book.max_total_exposure_fraction
    if exposure.total + proposed.notional > total_cap:
        return _deny(
            RiskReasonCode.PORTFOLIO_TOTAL_EXPOSURE_LIMIT,
            f"Entry of {_quote(proposed.notional)} would take the portfolio's exposure from "
            f"{_quote(exposure.total)} above its cap of {_quote(total_cap)} "
            "(max_total_exposure_fraction times capital).",
        )
    asset = asset_of(proposed.product_id)
    held = exposure.assets.get(asset, Decimal("0"))
    asset_cap = book.capital * book.max_per_asset_fraction
    if held + proposed.notional > asset_cap:
        return _deny(
            RiskReasonCode.PORTFOLIO_ASSET_EXPOSURE_LIMIT,
            f"Entry of {_quote(proposed.notional)} would take the portfolio's {asset} "
            f"exposure from {_quote(held)} above its cap of {_quote(asset_cap)} "
            "(max_per_asset_fraction times capital).",
        )
    return _allow()


@dataclass(frozen=True, slots=True)
class PortfolioExposure:
    """One portfolio's exposure as the entry gate counts it: total and per base asset."""

    total: Decimal
    assets: Mapping[str, Decimal]


def portfolio_exposure(
    portfolio_id: UUID, snapshots: Sequence[DeploymentSnapshot]
) -> PortfolioExposure:
    """Position cost plus working entries of every book tagged with this portfolio."""
    members = tuple(item for item in snapshots if item.deployment.portfolio_id == portfolio_id)
    total = sum((_marked_exposure(item) for item in members), Decimal("0"))
    assets: dict[str, Decimal] = {}
    for item in members:
        for asset in sorted({asset_of(product) for product in _book_products(item)}):
            assets[asset] = assets.get(asset, Decimal("0")) + _asset_exposure(item, asset)
    return PortfolioExposure(total=total, assets=assets)


def asset_of(product_id: str) -> str:
    """Base asset of one spot product (the whole id when it is not a spot pair)."""
    return base_currency(product_id) if is_spot_product_id(product_id) else product_id


def _asset_exposure(snapshot: DeploymentSnapshot, asset: str) -> Decimal:
    """Position cost plus working entry remainders on every product of one base asset."""
    return sum(
        (
            product_exposure(snapshot, product)
            for product in sorted(_book_products(snapshot))
            if asset_of(product) == asset
        ),
        Decimal("0"),
    )


def _quote(amount: Decimal) -> str:
    """Render a quote amount for a verdict detail (two decimals, no exponent)."""
    return f"{amount.quantize(Decimal('0.01')):f}"
