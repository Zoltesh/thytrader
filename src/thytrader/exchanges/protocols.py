"""Provider-neutral exchange account and live-order contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from decimal import Decimal

    from thytrader.exchanges.fees import FeeProfile
    from thytrader.exchanges.futures_models import (
        FuturesBalanceSummary,
        FuturesMarginWindow,
        FuturesPosition,
    )
    from thytrader.exchanges.models import ExchangeBalance


class ExchangeAccount(Protocol):
    """Read-only exchange capabilities used by portfolio aggregation."""

    async def list_balances(self) -> tuple[ExchangeBalance, ...]:
        """Return all non-empty exchange balances."""
        ...

    async def get_permissions(self) -> tuple[str, ...]:
        """Return detected key permissions without enforcing a permission ceiling."""
        ...

    async def get_usd_price(self, currency: str) -> Decimal | None:
        """Return a direct USD spot price when one exists."""
        ...

    async def get_fee_profile(self) -> FeeProfile:
        """Return the current 30-day volume and fee tier details."""
        ...


class FuturesAccountReader(Protocol):
    """Read-only CFM account reads (GET only; ADR 0127)."""

    async def balance_summary(self) -> FuturesBalanceSummary:
        """Return the balance summary or raise ``FuturesAccountReadError``."""
        ...

    async def positions(self) -> tuple[FuturesPosition, ...]:
        """Return every open position or raise ``FuturesAccountReadError``."""
        ...

    async def intraday_margin_setting(self) -> str:
        """Return the intraday margin setting token or raise ``FuturesAccountReadError``."""
        ...

    async def current_margin_window(self) -> FuturesMarginWindow:
        """Return the margin window in effect or raise ``FuturesAccountReadError``."""
        ...
