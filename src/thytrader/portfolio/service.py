"""Portfolio aggregation over a provider-neutral exchange boundary."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.portfolio.models import Money, Portfolio, PortfolioAsset, PortfolioConnection

if TYPE_CHECKING:
    from thytrader.exchanges.protocols import ExchangeAccount


class PortfolioService:
    """Build exact, point-in-time portfolio snapshots."""

    def __init__(self, exchange: ExchangeAccount, *, demo: bool = False) -> None:
        """Initialize the service with a read-only exchange account."""
        self._exchange = exchange
        self._demo = demo

    async def get_portfolio(self) -> Portfolio:
        """Fetch balances, value direct USD markets, and report all permissions."""
        balances = await self._exchange.list_balances()
        permissions = await self._exchange.get_permissions()
        assets: list[PortfolioAsset] = []
        unvalued: list[str] = []
        total_value = Decimal("0")

        for balance in balances:
            price = await self._price_for(balance.currency)
            value = None if price is None else Money(amount=balance.total * price)
            if value is None:
                unvalued.append(balance.currency)
            else:
                total_value += value.amount
            assets.append(
                PortfolioAsset(
                    currency=balance.currency,
                    name=balance.name,
                    available=balance.available,
                    hold=balance.hold,
                    total=balance.total,
                    value=value,
                )
            )

        return Portfolio(
            as_of=datetime.now(UTC),
            connection=PortfolioConnection(
                provider="coinbase",
                status="demo" if self._demo else "connected",
                permissions=permissions,
            ),
            demo=self._demo,
            total_value=Money(amount=total_value.quantize(Decimal("0.01"))),
            assets=tuple(assets),
            unvalued_assets=tuple(unvalued),
        )

    async def _price_for(self, currency: str) -> Decimal | None:
        """Resolve stable USD at par and delegate all other direct markets."""
        if currency in {"USD", "USDC"}:
            return Decimal("1")
        return await self._exchange.get_usd_price(currency)
