"""Behavioral tests for the Coinbase historical market-data adapter."""

import asyncio
from datetime import UTC, datetime
from typing import Any

from thytrader.exchanges.coinbase_market_data import CoinbaseMarketData
from thytrader.market_data.models import CandleInterval


class StubResponse:
    """Coinbase SDK response exposing a fixed boundary payload."""

    def __init__(self, payload: dict[str, Any]) -> None:
        """Store one synthetic SDK payload."""
        self._payload = payload

    def to_dict(self) -> dict[str, Any]:
        """Return a defensive copy of the synthetic payload."""
        return dict(self._payload)


class StubCoinbaseMarketClient:
    """Small public-SDK-shaped client for exact product/candle fixtures."""

    def __init__(self) -> None:
        """Track live-market calls for request-boundary verification."""
        self.candle_calls: list[tuple[str, str, str, str, int]] = []
        self.product_catalog_calls: list[tuple[str | None, bool | None, bool | None]] = []

    def get_products(
        self,
        limit: int | None = None,
        offset: int | None = None,
        product_type: str | None = None,
        product_ids: list[str] | None = None,
        contract_expiry_type: str | None = None,
        expiring_contract_status: str | None = None,
        get_tradability_status: bool | None = False,
        get_all_products: bool | None = False,
    ) -> StubResponse:
        """Return a compact spot catalog through the official SDK-shaped call."""
        del limit, offset, product_ids, contract_expiry_type, expiring_contract_status
        self.product_catalog_calls.append((product_type, get_tradability_status, get_all_products))
        return StubResponse(
            {
                "products": [
                    {
                        "product_id": "BTC-USD",
                        "base_currency_id": "BTC",
                        "quote_currency_id": "USD",
                        "price_increment": "0.01",
                        "base_increment": "0.00000001",
                        "quote_increment": "0.01",
                        "base_min_size": "0.0001",
                        "quote_min_size": "1",
                        "is_disabled": False,
                        "trading_disabled": False,
                    },
                    {
                        "product_id": "ETH-USD",
                        "base_currency_id": "ETH",
                        "quote_currency_id": "USD",
                        "price_increment": "0.01",
                        "base_increment": "0.00000001",
                        "quote_increment": "0.01",
                        "base_min_size": "0.001",
                        "quote_min_size": "1",
                        "is_disabled": True,
                        "trading_disabled": True,
                    },
                ]
            }
        )

    def get_product(self, product_id: str) -> StubResponse:
        """Return a tradable Coinbase spot product payload."""
        assert product_id == "BTC-USD"
        return StubResponse(
            {
                "product_id": "BTC-USD",
                "base_currency_id": "BTC",
                "quote_currency_id": "USD",
                "price_increment": "0.01",
                "base_increment": "0.00000001",
                "quote_increment": "0.01",
                "base_min_size": "0.0001",
                "quote_min_size": "1",
                "is_disabled": False,
                "trading_disabled": False,
            }
        )

    def get_candles(
        self,
        product_id: str,
        start: str,
        end: str,
        granularity: str,
        limit: int | None = None,
    ) -> StubResponse:
        """Return completed data plus a current, intentionally incomplete candle."""
        assert limit is not None
        self.candle_calls.append((product_id, start, end, granularity, limit))
        return StubResponse(
            {
                "candles": [
                    {
                        "start": "1785196800",
                        "open": "100",
                        "high": "110",
                        "low": "90",
                        "close": "105",
                        "volume": "12.5",
                    },
                    {
                        "start": "1785200400",
                        "open": "105",
                        "high": "115",
                        "low": "100",
                        "close": "112",
                        "volume": "10",
                    },
                    {
                        "start": "1785207600",
                        "open": "112",
                        "high": "118",
                        "low": "110",
                        "close": "116",
                        "volume": "8",
                    },
                    {
                        "start": "1785214800",
                        "open": "116",
                        "high": "120",
                        "low": "114",
                        "close": "119",
                        "volume": "3",
                    },
                ]
            }
        )


def test_coinbase_market_data_builds_exact_preview_and_reports_upstream_gaps() -> None:
    """A Coinbase payload should produce validated completed candles and gap metadata."""
    client = StubCoinbaseMarketClient()
    now = datetime(2026, 7, 28, 5, 30, tzinfo=UTC)

    preview = asyncio.run(
        CoinbaseMarketData(client).get_recent_preview("BTC-USD", CandleInterval.ONE_HOUR, now)
    )

    assert preview.product.product_id == "BTC-USD"
    assert preview.product.price_increment.as_tuple().exponent == -2
    assert preview.quality.candle_count == 3
    assert preview.quality.missing_intervals == 1
    assert preview.quality.is_stale is False
    assert client.candle_calls == [
        (
            "BTC-USD",
            str(int((now - CandleInterval.ONE_HOUR.duration * 25).timestamp())),
            str(int(now.timestamp())),
            "ONE_HOUR",
            350,
        )
    ]


def test_coinbase_market_data_lists_normalized_spot_products() -> None:
    """The adapter must ask Coinbase for all tradability-aware spot products once."""
    client = StubCoinbaseMarketClient()

    products = asyncio.run(CoinbaseMarketData(client).list_products())

    assert [product.product_id for product in products] == ["BTC-USD", "ETH-USD"]
    assert products[1].trading_enabled is False
    assert client.product_catalog_calls == [("SPOT", True, True)]
