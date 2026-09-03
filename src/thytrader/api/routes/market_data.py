"""Read-only market-data diagnostics HTTP presentation."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal  # noqa: TC003
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, field_serializer

from thytrader.api.dependencies import get_dataset_store, get_market_data_service
from thytrader.market_data.datasets import DatasetManifest, DatasetStore  # noqa: TC001
from thytrader.market_data.models import CandleRangeReport, MarketDataPreview  # noqa: TC001
from thytrader.market_data.service import MarketDataService  # noqa: TC001

router = APIRouter(prefix="/api/v1/market-data", tags=["market-data"])


class ProductResponse(BaseModel):
    """Browser-safe Coinbase spot-product constraints with exact decimal serialization."""

    model_config = ConfigDict(from_attributes=True)
    product_id: str
    base_currency: str
    quote_currency: str
    price_increment: Decimal
    base_increment: Decimal
    quote_increment: Decimal
    base_min_size: Decimal
    quote_min_size: Decimal
    trading_enabled: bool

    @field_serializer(
        "price_increment", "base_increment", "quote_increment", "base_min_size", "quote_min_size"
    )
    def serialize_decimal(self, value: Decimal) -> str:
        """Preserve exact venue constraints across the browser API boundary."""
        return format(value, "f")


class MarketDataQualityResponse(BaseModel):
    """Observable candle completeness and freshness facts."""

    candle_count: int
    gap_count: int
    missing_intervals: int
    latest_completed_at: datetime | None
    stale: bool


class MarketDataPreviewResponse(BaseModel):
    """Browser-safe current market-data diagnostic for the supported product/timeframe."""

    as_of: datetime
    product: ProductResponse
    timeframe: Literal["1h"]
    quality: MarketDataQualityResponse


class MarketDataRangeResponse(BaseModel):
    """Browser-safe seven-day 1h completeness report without candle payloads or persistence."""

    starts_at: datetime
    ends_at: datetime
    timeframe: Literal["1h"]
    requested_candle_count: int
    received_candle_count: int
    gap_count: int
    missing_intervals: int
    complete: bool


class ProductCatalogResponse(BaseModel):
    """Browser-safe deterministic set of selectable USD spot products."""

    products: tuple[ProductResponse, ...]


class DatasetResponse(BaseModel):
    """Browser-safe immutable dataset identity and complete evaluation coverage."""

    provider: str
    product_id: str
    timeframe: Literal["1h"]
    starts_at: datetime
    ends_at: datetime
    received_candle_count: int
    content_fingerprint: str


class DatasetCatalogResponse(BaseModel):
    """Verified local datasets selectable by browser research workflows."""

    datasets: tuple[DatasetResponse, ...]


class LatestDatasetCatalogResponse(BaseModel):
    """One latest verified revision per market for launch-form selection."""

    datasets: tuple[DatasetResponse, ...]


class MarketDataErrorDetail(BaseModel):
    """Stable redacted market-data upstream failure detail."""

    code: Literal["market_data_unavailable"]
    message: str


class MarketDataErrorResponse(BaseModel):
    """FastAPI-compatible market-data failure envelope."""

    detail: MarketDataErrorDetail


def _unavailable() -> HTTPException:
    """Build the stable redacted response for any provider failure."""
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail={
            "code": "market_data_unavailable",
            "message": "Market data is temporarily unavailable. Try again shortly.",
        },
    )


@router.get(
    "/products",
    response_model=ProductCatalogResponse,
    responses={status.HTTP_502_BAD_GATEWAY: {"model": MarketDataErrorResponse}},
)
async def get_market_data_products(
    service: Annotated[MarketDataService, Depends(get_market_data_service)],
) -> ProductCatalogResponse:
    """Return enabled USD spot products available for the read-only selector."""
    try:
        products = await service.list_enabled_usd_spot_products()
    except Exception:  # noqa: BLE001 - provider failures are intentionally redacted at the API boundary.
        raise _unavailable() from None
    return ProductCatalogResponse(
        products=tuple(ProductResponse.model_validate(product) for product in products)
    )


@router.get("/datasets", response_model=DatasetCatalogResponse)
async def get_verified_datasets(
    store: Annotated[DatasetStore, Depends(get_dataset_store)],
) -> DatasetCatalogResponse:
    """Return all complete local manifests after re-verifying their immutable contents."""
    manifests = store.list_verified()
    return DatasetCatalogResponse(
        datasets=tuple(_to_dataset_response(manifest) for manifest in manifests)
    )


@router.get("/datasets/latest", response_model=LatestDatasetCatalogResponse)
async def get_latest_verified_datasets(
    store: Annotated[DatasetStore, Depends(get_dataset_store)],
) -> LatestDatasetCatalogResponse:
    """Return the newest verified revision per market for launch selection.

    Cumulative worker revisions make full-history listings expensive and
    redundant for the launch form: the newest revision per market is a strict
    superset of its predecessors, and launch submission resolves exact
    fingerprints regardless of which listing served the selection.
    """
    manifests = store.list_latest_verified()
    return LatestDatasetCatalogResponse(
        datasets=tuple(_to_dataset_response(manifest) for manifest in manifests)
    )


@router.get(
    "/preview",
    response_model=MarketDataPreviewResponse,
    responses={status.HTTP_502_BAD_GATEWAY: {"model": MarketDataErrorResponse}},
)
async def get_market_data_preview(
    service: Annotated[MarketDataService, Depends(get_market_data_service)],
    product_id: Annotated[str, Query(pattern=r"^[A-Z0-9]{2,20}-USD$")] = "BTC-USD",
) -> MarketDataPreviewResponse:
    """Return validated selected-USD-product hourly facts without upstream exceptions."""
    try:
        preview = await service.get_hourly_preview(product_id)
    except Exception:  # noqa: BLE001 - provider failures are intentionally redacted at the API boundary.
        raise _unavailable() from None
    return _to_preview_response(preview)


@router.get(
    "/range",
    response_model=MarketDataRangeResponse,
    responses={status.HTTP_502_BAD_GATEWAY: {"model": MarketDataErrorResponse}},
)
async def get_market_data_range(
    service: Annotated[MarketDataService, Depends(get_market_data_service)],
    product_id: Annotated[str, Query(pattern=r"^[A-Z0-9]{2,20}-USD$")] = "BTC-USD",
) -> MarketDataRangeResponse:
    """Return a selected product's recent seven-day hourly completeness report."""
    try:
        report = await service.get_recent_hourly_range(product_id)
    except Exception:  # noqa: BLE001 - provider failures are intentionally redacted at the API boundary.
        raise _unavailable() from None
    return _to_range_response(report)


def _to_dataset_response(manifest: DatasetManifest) -> DatasetResponse:
    """Map a re-verified immutable manifest into its browser selector representation."""
    return DatasetResponse(
        provider=manifest.provider,
        product_id=manifest.product_id,
        timeframe="1h",
        starts_at=datetime.fromisoformat(manifest.starts_at),
        ends_at=datetime.fromisoformat(manifest.ends_at),
        received_candle_count=manifest.received_candle_count,
        content_fingerprint=manifest.content_fingerprint,
    )


def _to_preview_response(preview: MarketDataPreview) -> MarketDataPreviewResponse:
    """Map one validated domain preview into its compact browser representation."""
    return MarketDataPreviewResponse(
        as_of=preview.as_of,
        product=ProductResponse.model_validate(preview.product),
        timeframe=preview.interval.value,
        quality=MarketDataQualityResponse(
            candle_count=preview.quality.candle_count,
            gap_count=preview.quality.gap_count,
            missing_intervals=preview.quality.missing_intervals,
            latest_completed_at=preview.quality.latest_completed_at,
            stale=preview.quality.is_stale,
        ),
    )


def _to_range_response(report: CandleRangeReport) -> MarketDataRangeResponse:
    """Map a validated range report into browser-safe completeness facts."""
    return MarketDataRangeResponse(
        starts_at=report.starts_at,
        ends_at=report.ends_at,
        timeframe="1h",
        requested_candle_count=report.requested_candle_count,
        received_candle_count=report.quality.candle_count,
        gap_count=report.quality.gap_count,
        missing_intervals=report.quality.missing_intervals,
        complete=report.complete,
    )
