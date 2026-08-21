"""Behavioral tests for portfolio aggregation and valuation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from thytrader.exchanges.fees import FeeProfile
from thytrader.exchanges.models import ExchangeBalance
from thytrader.portfolio.service import PortfolioService


class StubExchangeAccount:
    """Deterministic exchange boundary used by portfolio behavior tests."""

    async def list_balances(self) -> tuple[ExchangeBalance, ...]:
        """Return balances with both valued and unvalued assets."""
        return (
            ExchangeBalance(
                currency="USD",
                name="US Dollar",
                available=Decimal("100.25"),
                hold=Decimal("9.75"),
            ),
            ExchangeBalance(
                currency="BTC",
                name="Bitcoin",
                available=Decimal("0.5"),
                hold=Decimal("0.1"),
            ),
            ExchangeBalance(
                currency="OBSCURE",
                name="Obscure",
                available=Decimal("2"),
                hold=Decimal("0"),
            ),
        )

    async def get_permissions(self) -> tuple[str, ...]:
        """Return extra permissions without restricting the connection."""
        return ("view", "trade", "transfer")

    async def get_usd_price(self, currency: str) -> Decimal | None:
        """Return one direct USD market and leave another unvalued."""
        return {"BTC": Decimal("60000"), "OBSCURE": None}[currency]

    async def get_fee_profile(self) -> FeeProfile:
        """Return deterministic fee profile."""
        return FeeProfile(
            taker_fee_rate=Decimal("0.0060"),
            maker_fee_rate=Decimal("0.0040"),
            usd_volume_30d=Decimal("15250.00"),
            fee_tier="Tier 1",
            as_of=datetime.now(UTC),
            source="coinbase",
        )


def test_portfolio_values_balances_and_accepts_extra_permissions() -> None:
    """All detected permissions should be reported while values remain exact."""
    portfolio = asyncio.run(PortfolioService(StubExchangeAccount()).get_portfolio())

    assert portfolio.total_value.amount == Decimal("36110.00")
    assert portfolio.connection.permissions == ("view", "trade", "transfer")
    assert portfolio.assets[1].total == Decimal("0.6")
    assert portfolio.assets[1].value is not None
    assert portfolio.assets[1].value.amount == Decimal("36000.0")
    assert portfolio.unvalued_assets == ("OBSCURE",)
