"""Deterministic demo exchange data for immediate UI evaluation."""

from datetime import UTC, datetime
from decimal import Decimal

from thytrader.exchanges.fees import FeeProfile
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

    async def get_fee_profile(self) -> FeeProfile:
        """Return a deterministic fee profile for demo display."""
        return FeeProfile(
            taker_fee_rate=Decimal("0.0060"),
            maker_fee_rate=Decimal("0.0040"),
            usd_volume_30d=Decimal("15250.00"),
            fee_tier="Tier 1",
            as_of=datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC),
            source="coinbase",
        )
