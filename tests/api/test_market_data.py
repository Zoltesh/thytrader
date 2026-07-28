"""Behavioral tests for the read-only market-data preview endpoint."""

from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.market_data.models import (
    Candle,
    CandleInterval,
    CandleQualityReport,
    CandleRangeReport,
    MarketDataPreview,
    MarketProduct,
)
from thytrader.market_data.service import MarketDataService


class StaticMarketDataProvider:
    """Provider boundary returning a deterministic validated preview."""

    async def get_recent_preview(
        self,
        product_id: str,
        interval: CandleInterval,
        now: datetime,
    ) -> MarketDataPreview:
        """Return one fresh preview and assert the supported dashboard selection."""
        assert product_id == "BTC-USD"
        assert interval is CandleInterval.ONE_HOUR
        starts_at = datetime(2026, 7, 28, 4, tzinfo=UTC)
        candle = Candle(
            starts_at=starts_at,
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("105"),
            volume=Decimal("12.5"),
        )
        return MarketDataPreview(
            product=MarketProduct(
                product_id=product_id,
                base_currency="BTC",
                quote_currency="USD",
                price_increment=Decimal("0.01"),
                base_increment=Decimal("0.00000001"),
                quote_increment=Decimal("0.01"),
                base_min_size=Decimal("0.0001"),
                quote_min_size=Decimal("1"),
                trading_enabled=True,
            ),
            interval=interval,
            as_of=now,
            quality=CandleQualityReport(
                candles=(candle,),
                candle_count=1,
                gap_count=0,
                missing_intervals=0,
                latest_completed_at=datetime(2026, 7, 28, 5, tzinfo=UTC),
                is_stale=False,
            ),
        )

    async def list_products(self) -> tuple[MarketProduct, ...]:
        """Return supported and intentionally excluded products for catalog filtering."""
        return (
            MarketProduct(
                product_id="SOL-USD",
                base_currency="SOL",
                quote_currency="USD",
                price_increment=Decimal("0.01"),
                base_increment=Decimal("0.0001"),
                quote_increment=Decimal("0.01"),
                base_min_size=Decimal("0.01"),
                quote_min_size=Decimal("1"),
                trading_enabled=True,
            ),
            MarketProduct(
                product_id="ETH-USD",
                base_currency="ETH",
                quote_currency="USD",
                price_increment=Decimal("0.01"),
                base_increment=Decimal("0.00000001"),
                quote_increment=Decimal("0.01"),
                base_min_size=Decimal("0.001"),
                quote_min_size=Decimal("1"),
                trading_enabled=False,
            ),
            MarketProduct(
                product_id="BTC-USD",
                base_currency="BTC",
                quote_currency="USD",
                price_increment=Decimal("0.01"),
                base_increment=Decimal("0.00000001"),
                quote_increment=Decimal("0.01"),
                base_min_size=Decimal("0.0001"),
                quote_min_size=Decimal("1"),
                trading_enabled=True,
            ),
            MarketProduct(
                product_id="BTC-EUR",
                base_currency="BTC",
                quote_currency="EUR",
                price_increment=Decimal("0.01"),
                base_increment=Decimal("0.00000001"),
                quote_increment=Decimal("0.01"),
                base_min_size=Decimal("0.0001"),
                quote_min_size=Decimal("1"),
                trading_enabled=True,
            ),
        )


class FailingMarketDataProvider:
    """Provider fake that proves upstream failures remain redacted."""

    async def list_products(self) -> tuple[MarketProduct, ...]:
        """Raise the same simulated provider failure for catalog requests."""
        message = "upstream token should not reach the browser"
        raise RuntimeError(message)

    async def get_recent_preview(
        self,
        product_id: str,
        interval: CandleInterval,
        now: datetime,
    ) -> MarketDataPreview:
        """Raise one synthetic sensitive provider failure."""
        del product_id, interval, now
        raise RuntimeError("synthetic market-data secret detail")


def test_market_data_preview_returns_exact_constraints_and_quality_metadata() -> None:
    """The dashboard contract should expose only validated, browser-safe market-data facts."""
    app = create_app(
        Settings(_env_file=None),
        market_data_service=MarketDataService(StaticMarketDataProvider()),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/market-data/preview")

    assert response.status_code == 200
    assert response.json() == {
        "as_of": response.json()["as_of"],
        "product": {
            "product_id": "BTC-USD",
            "base_currency": "BTC",
            "quote_currency": "USD",
            "price_increment": "0.01",
            "base_increment": "0.00000001",
            "quote_increment": "0.01",
            "base_min_size": "0.0001",
            "quote_min_size": "1",
            "trading_enabled": True,
        },
        "timeframe": "1h",
        "quality": {
            "candle_count": 1,
            "gap_count": 0,
            "missing_intervals": 0,
            "latest_completed_at": "2026-07-28T05:00:00Z",
            "stale": False,
        },
    }


def test_market_data_products_returns_sorted_enabled_usd_spot_catalog() -> None:
    """The browser catalog should expose only deterministic selectable USD spot products."""
    app = create_app(
        Settings(_env_file=None),
        market_data_service=MarketDataService(StaticMarketDataProvider()),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/market-data/products")

    assert response.status_code == 200
    assert response.json() == {
        "products": [
            {
                "product_id": "BTC-USD",
                "base_currency": "BTC",
                "quote_currency": "USD",
                "price_increment": "0.01",
                "base_increment": "0.00000001",
                "quote_increment": "0.01",
                "base_min_size": "0.0001",
                "quote_min_size": "1",
                "trading_enabled": True,
            },
            {
                "product_id": "SOL-USD",
                "base_currency": "SOL",
                "quote_currency": "USD",
                "price_increment": "0.01",
                "base_increment": "0.0001",
                "quote_increment": "0.01",
                "base_min_size": "0.01",
                "quote_min_size": "1",
                "trading_enabled": True,
            },
        ]
    }


def test_market_data_preview_uses_demo_data_without_coinbase_credentials() -> None:
    """A clean install should render the market-data panel without making network calls."""
    app = create_app(Settings(_env_file=None))

    with TestClient(app) as client:
        response = client.get("/api/v1/market-data/preview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["product"]["product_id"] == "BTC-USD"
    assert payload["timeframe"] == "1h"
    assert payload["quality"]["candle_count"] == 24
    assert payload["quality"]["gap_count"] == 0
    assert payload["quality"]["missing_intervals"] == 0
    assert payload["quality"]["stale"] is False


def test_market_data_products_uses_demo_catalog_without_coinbase_credentials() -> None:
    """A clean install should offer multiple deterministic USD products to the dashboard."""
    app = create_app(Settings(_env_file=None))

    with TestClient(app) as client:
        response = client.get("/api/v1/market-data/products")

    assert response.status_code == 200
    assert [product["product_id"] for product in response.json()["products"]] == [
        "BTC-USD",
        "ETH-USD",
        "SOL-USD",
    ]


def test_market_data_preview_uses_selected_demo_product() -> None:
    """The selected catalog product must drive the read-only preview request."""
    app = create_app(Settings(_env_file=None))

    with TestClient(app) as client:
        response = client.get("/api/v1/market-data/preview?product_id=ETH-USD")

    assert response.status_code == 200
    assert response.json()["product"]["product_id"] == "ETH-USD"


def test_market_data_preview_redacts_upstream_failures() -> None:
    """A provider failure must preserve a stable error contract without unsafe detail."""
    app = create_app(
        Settings(_env_file=None),
        market_data_service=MarketDataService(FailingMarketDataProvider()),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/market-data/preview")

    assert response.status_code == 502
    assert response.json() == {
        "detail": {
            "code": "market_data_unavailable",
            "message": "Market data is temporarily unavailable. Try again shortly.",
        }
    }
    assert "synthetic market-data secret detail" not in response.text


class RangeMarketDataProvider(StaticMarketDataProvider):
    """Preview provider extended with deterministic complete range coverage."""

    async def get_historical_range(
        self,
        product_id: str,
        interval: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Return browser-safe complete coverage facts for the requested diagnostic range."""
        preview = await self.get_recent_preview(product_id, interval, now)
        return CandleRangeReport(
            starts_at=starts_at,
            ends_at=ends_at,
            requested_candle_count=(ends_at - starts_at) // interval.duration,
            quality=preview.quality,
            complete=True,
        )


def test_market_data_range_returns_requested_and_received_coverage() -> None:
    """The browser must see explicit range completeness rather than only a recent preview count."""
    app = create_app(
        Settings(_env_file=None),
        market_data_service=MarketDataService(RangeMarketDataProvider()),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/market-data/range")

    assert response.status_code == 200
    assert response.json()["timeframe"] == "1h"
    assert response.json()["requested_candle_count"] == 168
    assert response.json()["received_candle_count"] == 1
    assert response.json()["complete"] is True
