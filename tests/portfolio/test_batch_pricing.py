"""Portfolio latency regression: bounded venue batches, not one round trip per asset."""

import asyncio
from decimal import Decimal
from typing import Any

from tests.exchanges.test_coinbase import StubCoinbaseClient, StubResponse
from thytrader.exchanges.coinbase import CoinbaseAccount
from thytrader.portfolio.service import PortfolioService


class BatchClient(StubCoinbaseClient):
    """Synthetic SDK transport counting actual valuation round trips."""

    def __init__(self) -> None:
        """Record requests independently of portfolio presentation."""
        super().__init__()
        self.batches: list[list[str]] = []
        self.singles: list[str] = []
        self.balance_reads = 0
        self.permission_reads = 0

    def get_accounts(self, *, limit: int, cursor: str | None = None) -> StubResponse:
        """Return many nonzero dust assets, duplicate wallets and distinct cash."""
        assert limit == 250 and cursor is None
        self.balance_reads += 1
        currencies = [*(f"COIN{i}" for i in range(205)), "COIN0", "USD", "USDC", "USDT"]
        return StubResponse(
            {
                "accounts": [
                    {
                        "name": currency,
                        "currency": currency,
                        "available_balance": {"value": "0.001", "currency": currency},
                        "hold": {"value": "0.002", "currency": currency},
                        "active": True,
                    }
                    for currency in currencies
                ],
                "has_next": False,
            }
        )

    def get_api_key_permissions(self) -> StubResponse:
        """Count fresh permission reads, which must never be price-cached."""
        self.permission_reads += 1
        return super().get_api_key_permissions()

    def get_product(self, product_id: str) -> StubResponse:
        """Model the original serial path without real requests or timing noise."""
        self.singles.append(product_id)
        return StubResponse({"product_id": product_id, "price": "2.5"})

    def get_products(self, **kwargs: Any) -> StubResponse:
        """Model the official filtered List Products SDK response."""
        assert kwargs["product_type"] == "SPOT"
        assert kwargs["get_all_products"] is True
        ids = kwargs["product_ids"]
        assert 0 < len(ids) <= 100
        assert kwargs["limit"] == len(ids)
        self.batches.append(list(ids))
        return StubResponse(
            {"products": [{"product_id": item, "price": "2.5"} for item in reversed(ids)]}
        )


def test_portfolio_prices_dust_in_bounded_batches_without_caching_account_reads() -> None:
    """Many dust wallets cost three round trips, preserving order and exact totals."""
    client = BatchClient()
    service = PortfolioService(CoinbaseAccount(client, unsupported_products=set()))
    for _ in range(2):
        portfolio = asyncio.run(service.get_portfolio())
        assert [asset.currency for asset in portfolio.assets] == [
            *(f"COIN{i}" for i in range(205)),
            "COIN0",
            "USD",
            "USDC",
            "USDT",
        ]
        assert portfolio.assets[0].value == portfolio.assets[205].value
        assert portfolio.assets[0].value is not None
        assert portfolio.assets[0].value.amount == Decimal("0.0075")
        assert portfolio.total_value.amount == Decimal("1.55")
        assert [(total.currency, total.amount) for total in portfolio.totals] == [
            ("USD", Decimal("1.55")),
            ("USDC", Decimal("0.00")),
            ("USDT", Decimal("0.00")),
        ]
    assert client.singles == []
    assert [len(batch) for batch in client.batches] == [100, 100, 5, 100, 100, 5]
    assert client.balance_reads == client.permission_reads == 2
