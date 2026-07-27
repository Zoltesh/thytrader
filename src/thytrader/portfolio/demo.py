"""Deterministic demo exchange data for immediate UI evaluation."""

from decimal import Decimal

from thytrader.exchanges.models import ExchangeBalance


class DemoExchangeAccount:
    """Provide realistic local portfolio data without external credentials."""

    async def list_balances(self) -> tuple[ExchangeBalance, ...]:
        """Return a small deterministic portfolio."""
        return (
            ExchangeBalance("BTC", "Bitcoin", Decimal("0.75"), Decimal("0.01")),
            ExchangeBalance("ETH", "Ethereum", Decimal("2.25"), Decimal("0")),
            ExchangeBalance("USDC", "USD Coin", Decimal("1250.00"), Decimal("0")),
        )

    async def get_permissions(self) -> tuple[str, ...]:
        """Show the sufficient permissions planned for the operator key."""
        return ("view", "trade")

    async def get_usd_price(self, currency: str) -> Decimal | None:
        """Return deterministic direct USD prices for demo assets."""
        return {"BTC": Decimal("120000"), "ETH": Decimal("3263.1866666667")}.get(currency)
