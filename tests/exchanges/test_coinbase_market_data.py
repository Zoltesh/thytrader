"""Behavioral tests for the Coinbase historical market-data adapter."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from thytrader.exchanges.coinbase_market_data import (
    CoinbaseMarketData,
    CoinbaseMarketDataError,
)
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


class PagedCoinbaseMarketClient(StubCoinbaseMarketClient):
    """SDK-shaped client that generates every requested hourly candle for paging tests."""

    def get_candles(
        self,
        product_id: str,
        start: str,
        end: str,
        granularity: str,
        limit: int | None = None,
    ) -> StubResponse:
        """Return exact candles in the requested half-open epoch interval."""
        assert limit is not None
        self.candle_calls.append((product_id, start, end, granularity, limit))
        return StubResponse(
            {
                "candles": [
                    {
                        "start": str(epoch),
                        "open": "100",
                        "high": "110",
                        "low": "90",
                        "close": "105",
                        "volume": "12.5",
                    }
                    for epoch in range(int(start), int(end), 60 * 60)
                ]
            }
        )


class BoundaryCandleCoinbaseMarketClient(PagedCoinbaseMarketClient):
    """SDK-shaped client that includes Coinbase's extra end-boundary candle."""

    def get_candles(
        self,
        product_id: str,
        start: str,
        end: str,
        granularity: str,
        limit: int | None = None,
    ) -> StubResponse:
        """Return requested candles plus an open candle at the exclusive range boundary."""
        response = super().get_candles(product_id, start, end, granularity, limit)
        payload = response.to_dict()
        candles = payload["candles"]
        assert isinstance(candles, list)
        candles.append(
            {
                "start": end,
                "open": "105",
                "high": "110",
                "low": "100",
                "close": "108",
                "volume": "1",
            }
        )
        return StubResponse(payload)


class EmptyCandleCoinbaseMarketClient(StubCoinbaseMarketClient):
    """SDK-shaped client returning no candles for boundary arithmetic tests."""

    def get_candles(
        self,
        product_id: str,
        start: str,
        end: str,
        granularity: str,
        limit: int | None = None,
    ) -> StubResponse:
        """Return an empty validated-shaped candle response."""
        assert limit is not None
        self.candle_calls.append((product_id, start, end, granularity, limit))
        return StubResponse({"candles": []})


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


def test_coinbase_market_data_pages_explicit_hourly_range_without_losing_coverage() -> None:
    """A range larger than one Coinbase page must retain complete consecutive coverage."""
    client = PagedCoinbaseMarketClient()
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    ends_at = datetime(2026, 7, 16, 1, tzinfo=UTC)

    report = asyncio.run(
        CoinbaseMarketData(client).get_historical_range(
            "BTC-USD",
            CandleInterval.ONE_HOUR,
            starts_at,
            ends_at,
            now=ends_at + CandleInterval.ONE_HOUR.duration,
        )
    )

    assert report.requested_candle_count == 361
    assert report.quality.candle_count == 361
    assert report.complete is True
    assert client.candle_calls == [
        (
            "BTC-USD",
            str(int(starts_at.timestamp())),
            str(int((starts_at + CandleInterval.ONE_HOUR.duration * 350).timestamp())),
            "ONE_HOUR",
            350,
        ),
        (
            "BTC-USD",
            str(int((starts_at + CandleInterval.ONE_HOUR.duration * 350).timestamp())),
            str(int(ends_at.timestamp())),
            "ONE_HOUR",
            350,
        ),
    ]


def test_coinbase_market_data_ignores_open_candle_at_exclusive_range_boundary() -> None:
    """An extra Coinbase boundary candle must not make an otherwise complete range fail closed."""
    client = BoundaryCandleCoinbaseMarketClient()
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    ends_at = datetime(2026, 7, 2, tzinfo=UTC)

    report = asyncio.run(
        CoinbaseMarketData(client).get_historical_range(
            "BTC-USD",
            CandleInterval.ONE_HOUR,
            starts_at,
            ends_at,
            now=ends_at + CandleInterval.ONE_HOUR.duration,
        )
    )

    assert report.requested_candle_count == 24
    assert report.quality.candle_count == 24
    assert report.complete is True


def test_coinbase_market_data_maps_recent_lower_boundary_overflow() -> None:
    """A minimum-date preview request must fail as a controlled adapter error."""
    with pytest.raises(CoinbaseMarketDataError, match="represent"):
        asyncio.run(
            CoinbaseMarketData(StubCoinbaseMarketClient()).get_recent_preview(
                "BTC-USD",
                CandleInterval.ONE_HOUR,
                datetime.min.replace(tzinfo=UTC),
            )
        )


def test_coinbase_market_data_maps_historical_page_boundary_overflow() -> None:
    """A maximum-date range must not leak page arithmetic OverflowError."""
    ends_at = datetime.max.replace(tzinfo=UTC)

    with pytest.raises(CoinbaseMarketDataError, match="represent"):
        asyncio.run(
            CoinbaseMarketData(EmptyCandleCoinbaseMarketClient()).get_historical_range(
                "BTC-USD",
                CandleInterval.ONE_HOUR,
                ends_at - timedelta(hours=1),
                ends_at,
                ends_at,
            )
        )
