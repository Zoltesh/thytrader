"""Behavioral tests for the Coinbase historical market-data adapter."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from thytrader.exchanges.coinbase_market_data import (
    CoinbaseMarketData,
    CoinbaseMarketDataError,
)
from thytrader.market_data.models import MAX_HISTORICAL_INTERVAL_COUNT, CandleInterval


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


_GRANULARITY_SECONDS = {
    "ONE_HOUR": 60 * 60,
    "FIVE_MINUTE": 5 * 60,
    "FIFTEEN_MINUTE": 15 * 60,
}


def _inclusive_epochs(start: str, end: str, granularity: str, limit: int) -> list[int]:
    """Mimic Coinbase: inclusive end, newest ``limit`` bars when the window is wider."""
    step = _GRANULARITY_SECONDS[granularity]
    epochs = list(range(int(start), int(end) + step, step))
    if len(epochs) > limit:
        return epochs[-limit:]
    return epochs


class PagedCoinbaseMarketClient(StubCoinbaseMarketClient):
    """SDK-shaped client that generates requested candles with Coinbase inclusive-end semantics."""

    def get_candles(
        self,
        product_id: str,
        start: str,
        end: str,
        granularity: str,
        limit: int | None = None,
    ) -> StubResponse:
        """Return exact candles in the requested inclusive epoch interval."""
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
                    for epoch in _inclusive_epochs(start, end, granularity, limit)
                ]
            }
        )


class BoundaryCandleCoinbaseMarketClient(PagedCoinbaseMarketClient):
    """SDK-shaped client that includes Coinbase's extra candle after the inclusive end."""

    def get_candles(
        self,
        product_id: str,
        start: str,
        end: str,
        granularity: str,
        limit: int | None = None,
    ) -> StubResponse:
        """Return requested candles plus the next bar after Coinbase's inclusive end."""
        response = super().get_candles(product_id, start, end, granularity, limit)
        payload = response.to_dict()
        candles = payload["candles"]
        assert isinstance(candles, list)
        step = _GRANULARITY_SECONDS[granularity]
        candles.append(
            {
                "start": str(int(end) + step),
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


class MalformedEpochCoinbaseMarketClient(StubCoinbaseMarketClient):
    """SDK-shaped client returning one candle with an invalid epoch text value."""

    def __init__(self, candle_start: str) -> None:
        """Store the malformed provider timestamp returned by every candle call."""
        super().__init__()
        self._candle_start = candle_start

    def get_candles(
        self,
        product_id: str,
        start: str,
        end: str,
        granularity: str,
        limit: int | None = None,
    ) -> StubResponse:
        """Return one structurally valid candle whose epoch conversion must fail closed."""
        assert limit is not None
        self.candle_calls.append((product_id, start, end, granularity, limit))
        return StubResponse(
            {
                "candles": [
                    {
                        "start": self._candle_start,
                        "open": "100",
                        "high": "110",
                        "low": "90",
                        "close": "105",
                        "volume": "12.5",
                    }
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
            str(int((starts_at + CandleInterval.ONE_HOUR.duration * 349).timestamp())),
            "ONE_HOUR",
            350,
        ),
        (
            "BTC-USD",
            str(int((starts_at + CandleInterval.ONE_HOUR.duration * 350).timestamp())),
            str(int((ends_at - CandleInterval.ONE_HOUR.duration).timestamp())),
            "ONE_HOUR",
            350,
        ),
    ]


def test_coinbase_market_data_keeps_oldest_bar_on_full_five_minute_page() -> None:
    """A 350-bar 5m page must not drop the first closed bar to Coinbase's newest-350 cap."""
    client = PagedCoinbaseMarketClient()
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    ends_at = starts_at + CandleInterval.FIVE_MINUTES.duration * 350

    report = asyncio.run(
        CoinbaseMarketData(client).get_historical_range(
            "BTC-USD",
            CandleInterval.FIVE_MINUTES,
            starts_at,
            ends_at,
            now=ends_at + CandleInterval.FIVE_MINUTES.duration,
        )
    )

    assert report.requested_candle_count == 350
    assert report.quality.candle_count == 350
    assert report.complete is True
    assert report.quality.candles[0].starts_at == starts_at
    assert client.candle_calls == [
        (
            "BTC-USD",
            str(int(starts_at.timestamp())),
            str(int((starts_at + CandleInterval.FIVE_MINUTES.duration * 349).timestamp())),
            "FIVE_MINUTE",
            350,
        )
    ]


def test_coinbase_market_data_pages_five_minute_range_past_legacy_interval_cap() -> None:
    """A 5m range longer than 4,032 bars must page at 350 without dropping the oldest bar."""
    client = PagedCoinbaseMarketClient()
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    bar_count = 4_033
    ends_at = starts_at + CandleInterval.FIVE_MINUTES.duration * bar_count

    report = asyncio.run(
        CoinbaseMarketData(client).get_historical_range(
            "BTC-USD",
            CandleInterval.FIVE_MINUTES,
            starts_at,
            ends_at,
            now=ends_at + CandleInterval.FIVE_MINUTES.duration,
        )
    )

    assert report.requested_candle_count == bar_count
    assert report.quality.candle_count == bar_count
    assert report.complete is True
    assert report.quality.candles[0].starts_at == starts_at
    assert len(client.candle_calls) == 12
    assert {call[4] for call in client.candle_calls} == {350}


def test_coinbase_market_data_keeps_oldest_bar_on_full_fifteen_minute_page() -> None:
    """A 350-bar 15m page must not drop the first closed bar to Coinbase's newest-350 cap."""
    client = PagedCoinbaseMarketClient()
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    ends_at = starts_at + CandleInterval.FIFTEEN_MINUTES.duration * 350

    report = asyncio.run(
        CoinbaseMarketData(client).get_historical_range(
            "BTC-USD",
            CandleInterval.FIFTEEN_MINUTES,
            starts_at,
            ends_at,
            now=ends_at + CandleInterval.FIFTEEN_MINUTES.duration,
        )
    )

    assert report.requested_candle_count == 350
    assert report.quality.candle_count == 350
    assert report.complete is True
    assert report.quality.candles[0].starts_at == starts_at
    assert client.candle_calls == [
        (
            "BTC-USD",
            str(int(starts_at.timestamp())),
            str(int((starts_at + CandleInterval.FIFTEEN_MINUTES.duration * 349).timestamp())),
            "FIFTEEN_MINUTE",
            350,
        )
    ]


def test_coinbase_market_data_rejects_five_minute_range_past_product_cap() -> None:
    """The product interval cap still fail-closes one request that exceeds 25,920 bars."""
    starts_at = datetime(2026, 7, 1, tzinfo=UTC)
    ends_at = starts_at + CandleInterval.FIVE_MINUTES.duration * (MAX_HISTORICAL_INTERVAL_COUNT + 1)

    with pytest.raises(CoinbaseMarketDataError, match="supported closed-candle"):
        asyncio.run(
            CoinbaseMarketData(EmptyCandleCoinbaseMarketClient()).get_historical_range(
                "BTC-USD",
                CandleInterval.FIVE_MINUTES,
                starts_at,
                ends_at,
                now=ends_at + CandleInterval.FIVE_MINUTES.duration,
            )
        )


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
    """A maximum-date closed range must not leak page arithmetic OverflowError."""
    ends_at = datetime.max.replace(tzinfo=UTC)
    starts_at = ends_at - timedelta(hours=1)

    report = asyncio.run(
        CoinbaseMarketData(EmptyCandleCoinbaseMarketClient()).get_historical_range(
            "BTC-USD",
            CandleInterval.ONE_HOUR,
            starts_at,
            ends_at,
            ends_at,
        )
    )

    assert report.complete is False
    assert report.quality.candle_count == 0


@pytest.mark.parametrize("candle_start", ["9" * 30, "²"])
def test_coinbase_market_data_rejects_unrepresentable_or_non_ascii_candle_epochs(
    candle_start: str,
) -> None:
    """Malformed upstream epoch text must map to the stable adapter error."""
    with pytest.raises(CoinbaseMarketDataError, match="epoch"):
        asyncio.run(
            CoinbaseMarketData(MalformedEpochCoinbaseMarketClient(candle_start)).get_recent_preview(
                "BTC-USD",
                CandleInterval.ONE_HOUR,
                datetime(2026, 7, 28, 5, 30, tzinfo=UTC),
            )
        )
