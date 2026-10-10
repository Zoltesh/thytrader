"""Behavioral tests for portfolio aggregation and valuation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

from thytrader.exchanges.fees import FeeProfile
from thytrader.exchanges.models import ExchangeBalance, ExchangeOpenOrder
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

    async def list_open_orders(self) -> tuple[ExchangeOpenOrder, ...]:
        """The stub venue rests no orders."""
        return ()


def test_portfolio_values_balances_and_accepts_extra_permissions() -> None:
    """All detected permissions should be reported while values remain exact."""
    portfolio = asyncio.run(PortfolioService(StubExchangeAccount()).get_portfolio())

    assert portfolio.total_value.amount == Decimal("36110.00")
    assert portfolio.connection.permissions == ("view", "trade", "transfer")
    assert portfolio.assets[1].total == Decimal("0.6")
    assert portfolio.assets[1].value is not None
    assert portfolio.assets[1].value.amount == Decimal("36000.0")
    assert portfolio.unvalued_assets == ("OBSCURE",)


def test_demo_flag_is_readable_for_research_fee_suggestions() -> None:
    """Fee suggestions fail closed on demo; live services expose demo=False."""
    demo_service = PortfolioService(StubExchangeAccount(), demo=True)
    live_service = PortfolioService(StubExchangeAccount(), demo=False)
    assert demo_service.demo is True
    assert live_service.demo is False


class _MixedCashAccount(StubExchangeAccount):
    """USD, USDC and USDT cash beside one USD-valued coin."""

    async def list_balances(self) -> tuple[ExchangeBalance, ...]:
        """Return all three cash currencies and BTC."""
        return (
            ExchangeBalance("USD", "US Dollar", Decimal("10.005"), Decimal("0")),
            ExchangeBalance("USDC", "USD Coin", Decimal("200.50"), Decimal("4.50")),
            ExchangeBalance("USDT", "Tether", Decimal("3"), Decimal("0")),
            ExchangeBalance("BTC", "Bitcoin", Decimal("0.001"), Decimal("0")),
        )


def test_cash_currencies_are_never_added_together_exactly() -> None:
    """Each cash balance keeps its own currency; only the labelled total adds them 1:1."""
    portfolio = asyncio.run(PortfolioService(_MixedCashAccount()).get_portfolio())
    values = {asset.currency: asset.value for asset in portfolio.assets}
    assert values["USDC"] is not None
    assert (values["USDC"].amount, values["USDC"].currency) == (Decimal("205.00"), "USDC")
    assert values["USDT"] is not None
    assert values["USDT"].currency == "USDT"
    assert values["BTC"] is not None
    assert values["BTC"].currency == "USD"
    assert [(total.currency, total.amount) for total in portfolio.totals] == [
        ("USD", Decimal("70.00")),
        ("USDC", Decimal("205.00")),
        ("USDT", Decimal("3.00")),
    ]
    assert portfolio.total_value_basis == "usd_pegged_approximate"
    assert portfolio.total_value.currency == "USD"
    assert portfolio.total_value.amount == Decimal("278.00")


def test_totals_omit_currencies_without_balances_and_skip_unvalued_assets() -> None:
    """An unvalued coin is excluded from every total, never counted as zero value."""
    portfolio = asyncio.run(PortfolioService(StubExchangeAccount()).get_portfolio())
    assert [(total.currency, total.amount) for total in portfolio.totals] == [
        ("USD", Decimal("36110.00"))
    ]
    assert portfolio.unvalued_assets == ("OBSCURE",)
