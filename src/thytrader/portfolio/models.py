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
    currency: Literal["USD"] = "USD"


@dataclass(frozen=True, slots=True)
class PortfolioAsset:
    """One asset balance with an optional USD valuation."""

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


@dataclass(frozen=True, slots=True)
class Portfolio:
    """A point-in-time portfolio snapshot."""

    as_of: datetime
    connection: PortfolioConnection
    demo: bool
    total_value: Money
    assets: tuple[PortfolioAsset, ...]
    unvalued_assets: tuple[str, ...]
