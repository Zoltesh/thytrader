"""Portfolio aggregation over a provider-neutral exchange boundary."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from thytrader.exchanges.read_errors import (
    ExchangeReadError,
    ExchangeReadFailure,
    ExchangeReadFailureKind,
    ExchangeReadOperation,
)
from thytrader.portfolio.models import (
    USD_PEGGED_APPROXIMATE,
    Money,
    Portfolio,
    PortfolioAsset,
    PortfolioConnection,
)

if TYPE_CHECKING:
    from thytrader.exchanges.fees import FeeProfile
    from thytrader.exchanges.models import ExchangeBalance, ExchangeOpenOrder
    from thytrader.exchanges.protocols import ExchangeAccount


# Valued in their own currency; USD, USDC and USDT are never added together exactly.
_CASH_CURRENCIES: tuple[str, ...] = ("USD", "USDC", "USDT")
_CENT = Decimal("0.01")


class PortfolioService:
    """Build exact, point-in-time portfolio snapshots."""

    def __init__(self, exchange: ExchangeAccount, *, demo: bool = False) -> None:
        """Initialize the service with a read-only exchange account."""
        self._exchange = exchange
        self._demo = demo

    @property
    def demo(self) -> bool:
        """Whether this service is using credential-less demo exchange data."""
        return self._demo

    async def get_portfolio(self) -> Portfolio:
        """Fetch balances, value them per currency, and report all permissions.

        Exact totals stay per currency. ``total_value`` is the explicitly labelled
        USD-pegged approximation of those totals added 1:1.
        """
        balances = await self._exchange.list_balances()
        permissions = await self._exchange.get_permissions()
        assets: list[PortfolioAsset] = []
        unvalued: list[str] = []
        totals: dict[str, Decimal] = {}

        for balance in balances:
            value = await self._value_of(balance)
            if value is None:
                unvalued.append(balance.currency)
            else:
                totals[value.currency] = totals.get(value.currency, Decimal(0)) + value.amount
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
            total_value=Money(
                amount=sum(totals.values(), Decimal(0)).quantize(_CENT), currency="USD"
            ),
            total_value_basis=USD_PEGGED_APPROXIMATE,
            totals=tuple(
                Money(amount=totals[currency].quantize(_CENT), currency=currency)
                for currency in _CASH_CURRENCIES
                if currency in totals
            ),
            assets=tuple(assets),
            unvalued_assets=tuple(unvalued),
        )

    async def _value_of(self, balance: ExchangeBalance) -> Money | None:
        """Value cash in its own currency and other assets through ``<asset>-USD``.

        A USDC balance is USDC, not USD: it is never relabelled or converted.
        """
        if balance.currency in _CASH_CURRENCIES:
            return Money(amount=balance.total, currency=balance.currency)
        price = await self._exchange.get_usd_price(balance.currency)
        if price is None:
            return None
        return Money(amount=balance.total * price, currency="USD")

    async def get_fee_profile(self) -> FeeProfile:
        """Fetch 30-day volume and current fee rates."""
        return await self._exchange.get_fee_profile()

    async def list_open_orders(self) -> tuple[ExchangeOpenOrder, ...]:
        """Fetch the venue's resting open orders through the neutral boundary.

        Read-only observation for venue-wide reconciliation (ADR 0114); this never
        creates, cancels, or replaces an order. The listing capability is optional on
        the account boundary: adapters without it fail closed with a typed read error
        so the reconciliation report degrades to an unknown listing instead of
        guessing an empty book.
        """
        listing = getattr(self._exchange, "list_open_orders", None)
        if listing is None:  # getattr is unavoidable: the protocol member is optional.
            raise ExchangeReadError(
                ExchangeReadFailure(
                    operation=ExchangeReadOperation.OPEN_ORDERS,
                    kind=ExchangeReadFailureKind.UNSUPPORTED,
                )
            )
        return await listing()

    async def list_futures_open_orders(self) -> tuple[ExchangeOpenOrder, ...]:
        """Fetch nonterminal CFM futures orders (read-only; all external, ADR 0127).

        Optional on the account boundary like ``list_open_orders``: an adapter without it
        fails closed with a typed read error, so the report says unknown, not none.
        """
        listing = getattr(self._exchange, "list_futures_open_orders", None)
        if listing is None:  # getattr is unavoidable: the protocol member is optional.
            raise ExchangeReadError(
                ExchangeReadFailure(
                    operation=ExchangeReadOperation.FUTURES_OPEN_ORDERS,
                    kind=ExchangeReadFailureKind.UNSUPPORTED,
                )
            )
        return await listing()
