"""Behavioral tests for the read-only market-data preview endpoint."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from threading import Event
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient
import pytest

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.market_data.datasets import DatasetManifest, DatasetStore
from thytrader.market_data.models import (
    Candle,
    CandleInterval,
    CandleQualityReport,
    CandleRangeReport,
    MarketDataPreview,
    MarketProduct,
)
from thytrader.market_data.quality import analyze_range
from thytrader.market_data.service import MarketDataService

if TYPE_CHECKING:
    from pathlib import Path


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


def _stored_dataset(root: Path) -> str:
    """Publish one complete verified fixture dataset and return its fingerprint."""
    starts_at = datetime(2026, 7, 1, 0, tzinfo=UTC)
    candles = (
        Candle(
            starts_at=starts_at,
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("105"),
            volume=Decimal("12.5"),
        ),
    )
    report = analyze_range(
        candles,
        CandleInterval.ONE_HOUR,
        starts_at,
        starts_at + CandleInterval.ONE_HOUR.duration,
        now=starts_at + CandleInterval.ONE_HOUR.duration,
    )
    return DatasetStore(root).write("coinbase", "BTC-USD", report).content_fingerprint


def test_market_data_latest_datasets_returns_one_revision_per_market(tmp_path: Path) -> None:
    """The launch-form catalog serves the newest revision without the full history."""
    fingerprint = _stored_dataset(tmp_path)
    app = create_app(
        Settings(_env_file=None, market_data_dataset_root=tmp_path),
        market_data_service=MarketDataService(StaticMarketDataProvider()),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/market-data/datasets/latest")

    assert response.status_code == 200
    assert response.json() == {
        "datasets": [
            {
                "provider": "coinbase",
                "product_id": "BTC-USD",
                "timeframe": "1h",
                "starts_at": "2026-07-01T00:00:00Z",
                "ends_at": "2026-07-01T01:00:00Z",
                "received_candle_count": 1,
                "content_fingerprint": fingerprint,
            }
        ]
    }


def test_market_data_latest_datasets_reports_empty_catalog_without_datasets(
    tmp_path: Path,
) -> None:
    """A dataset-free store must return an empty catalog instead of failing."""
    app = create_app(
        Settings(_env_file=None, market_data_dataset_root=tmp_path),
        market_data_service=MarketDataService(StaticMarketDataProvider()),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/market-data/datasets/latest")

    assert response.status_code == 200
    assert response.json() == {"datasets": []}


class _BlockingCatalogStore(DatasetStore):
    """Hold catalog work until a test proves another request can complete."""

    def __init__(self, root: Path) -> None:
        """Configure synchronization signals for a deliberately slow listing."""
        super().__init__(root)
        self.started = Event()
        self.release = Event()

    def _block(self) -> tuple[DatasetManifest, ...]:
        """Signal catalog entry and wait for the responsiveness assertion."""
        self.started.set()
        if not self.release.wait(timeout=2):
            raise TimeoutError("test did not release the blocking dataset catalog")
        return ()

    def list_verified(self) -> tuple[DatasetManifest, ...]:
        """Block the full-history listing."""
        return self._block()

    def list_latest_verified(self) -> tuple[DatasetManifest, ...]:
        """Block the latest-per-market listing."""
        return self._block()


@pytest.mark.parametrize(
    "catalog_path",
    ("/api/v1/market-data/datasets", "/api/v1/market-data/datasets/latest"),
)
def test_dataset_catalog_work_does_not_block_unrelated_api_requests(
    tmp_path: Path, catalog_path: str
) -> None:
    """Slow filesystem catalog work must run outside the API event loop."""
    app = create_app(
        Settings(_env_file=None, market_data_dataset_root=tmp_path),
        market_data_service=MarketDataService(StaticMarketDataProvider()),
    )
    store = _BlockingCatalogStore(tmp_path)
    app.state.dataset_store = store

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as executor:
        catalog_request = executor.submit(client.get, catalog_path)
        assert store.started.wait(timeout=1)
        health_request = executor.submit(client.get, "/health/live")
        try:
            health_response = health_request.result(timeout=0.5)
        finally:
            store.release.set()
        catalog_response = catalog_request.result(timeout=1)

    assert health_response.status_code == 200
    assert catalog_response.status_code == 200
