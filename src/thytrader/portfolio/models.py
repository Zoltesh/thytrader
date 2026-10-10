"""Exact portfolio models shared by API presentation code."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Money:
    """An exact amount in a named fiat currency."""

    amount: Decimal
    currency: str = "USDC"


@dataclass(frozen=True, slots=True)
class PortfolioAsset:
    """One asset balance with an optional valuation.

    USD, USDC and USDT balances are valued in their own currency; every other asset is
    valued in USD through its ``<asset>-USD`` market, or ``None`` when it has none.
    """

    currency: str
    name: str
    available: Decimal
    hold: Decimal
    total: Decimal
    value: Money | None


@dataclass(frozen=True, slots=True)
class PortfolioConnection:
    """Exchange connection state and detected permissions."""

    provider: Literal["coinbase"]
    status: Literal["connected", "demo"]
    permissions: tuple[str, ...]


TotalValueBasis = Literal["usd_pegged_approximate"]
USD_PEGGED_APPROXIMATE: TotalValueBasis = "usd_pegged_approximate"


@dataclass(frozen=True, slots=True)
class Portfolio:
    """A point-in-time portfolio snapshot.

    ``totals`` are the exact per-currency totals: USD (USD cash plus every asset valued
    through its ``<asset>-USD`` market), USDC and USDT, each summed only within its own
    currency. ``total_value`` is a labelled approximation (``total_value_basis`` is
    ``usd_pegged_approximate``): the same totals added at 1:1 in USD. It is a display
    and history figure only and never feeds risk, capital or accounting.
    """

    as_of: datetime
    connection: PortfolioConnection
    demo: bool
    total_value: Money
    total_value_basis: TotalValueBasis
    totals: tuple[Money, ...]
    assets: tuple[PortfolioAsset, ...]
    unvalued_assets: tuple[str, ...]
