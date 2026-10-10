"""Provider-neutral application service for read-only market-data diagnostics."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import time
from typing import Protocol, cast, runtime_checkable

from thytrader.market_data.instrument_catalog import (
    FuturesCatalogCache,
    build_instrument_catalog,
)
from thytrader.market_data.instrument_ids import is_futures_product_id
from thytrader.market_data.instruments import (
    FuturesCatalogProvider,
    Instrument,
    InstrumentCatalog,
    InstrumentKind,
)
from thytrader.market_data.models import (
    CandleInterval,
    CandleRangeReport,
    MarketDataPreview,
    MarketProduct,
)
from thytrader.market_data.products import SPOT_QUOTE_CURRENCIES
from thytrader.market_data.window_cache import DeployWindowCache


class MarketProductNotFoundError(LookupError):
    """Signal an authoritative provider lookup that proves a product does not exist."""


@dataclass(frozen=True, slots=True)
class ProductCatalogSnapshot:
    """One fresh provider observation with stable identity and exact product constraints."""

    products: tuple[MarketProduct, ...]
    observed_at: datetime
    fingerprint: str

    @property
    def enabled_products(self) -> tuple[MarketProduct, ...]:
        """Select supported enabled quote markets from this one exact observation."""
        return tuple(
            product
            for product in self.products
            if product.quote_currency in SPOT_QUOTE_CURRENCIES and product.trading_enabled
        )


@runtime_checkable
class ProductLookupProvider(Protocol):
    """Optional provider-neutral direct lookup for products omitted from a listing."""

    async def get_product(self, product_id: str) -> MarketProduct:
        """Return an authoritative product or raise an explicit not-found/unavailable error."""
        ...


class MarketDataProvider(Protocol):
    """Read-only preview and product-catalog capability used by dashboard presentation."""

    async def get_recent_preview(
        self, product_id: str, interval: CandleInterval, now: datetime
    ) -> MarketDataPreview:
        """Return product metadata plus validated recent closed candles."""
        ...

    async def list_products(self) -> tuple[MarketProduct, ...]:
        """Return the provider's current normalized spot-product catalog."""
        ...


class HistoricalMarketDataProvider(MarketDataProvider, Protocol):
    """Extended provider boundary required only by bounded range diagnostics."""

    async def get_historical_range(
        self,
        product_id: str,
        interval: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Return validated quality facts for one explicit closed-candle range."""
        ...


class MarketDataService:
    """Coordinate read-only previews and bounded diagnostics at UTC observation instants."""

    def __init__(
        self,
        provider: MarketDataProvider,
        *,
        window_cache: DeployWindowCache | None = None,
        futures_provider: FuturesCatalogProvider | None = None,
    ) -> None:
        """Initialize the service around a provider-neutral data boundary.

        ``window_cache`` remembers deploy-anchored execution windows for this service's
        provider (ADR 0113). Each service instance gets its own cache, so a venue
        credential swap starts empty and demo candles never mix with venue candles.
        ``futures_provider`` adds the read-only futures listing (ADR 0126); without it
        (demo mode) the instrument catalog is spot-only and futures ids are unknown.
        """
        self._provider = provider
        self.futures_catalog = (
            None if futures_provider is None else FuturesCatalogCache(futures_provider)
        )
        self.window_cache = window_cache if window_cache is not None else DeployWindowCache()
        self._catalog_lock = asyncio.Lock()
        self._catalog: ProductCatalogSnapshot | None = None
        self._catalog_expires_at = 0.0

    async def list_enabled_spot_products(self) -> tuple[MarketProduct, ...]:
        """Return deterministic selectable USD and USDC spot products without disabled markets."""
        products = (await self.catalog_snapshot()).products
        return tuple(
            sorted(
                (
                    product
                    for product in products
                    if product.quote_currency in SPOT_QUOTE_CURRENCIES and product.trading_enabled
                ),
                key=lambda product: product.product_id,
            )
        )

    async def catalog_snapshot(self) -> ProductCatalogSnapshot:
        """Share a 30-second observation; concurrent misses make one provider request."""
        async with self._catalog_lock:
            if self._catalog is None or time.monotonic() >= self._catalog_expires_at:
                products = await self._provider.list_products()
                if not products:
                    raise ValueError("The provider returned an empty product catalog.")
                self._set_catalog(products)
            if self._catalog is None:
                raise ValueError("Product catalog observation is unavailable.")
            return self._catalog

    async def enabled_spot_product(self, product_id: str) -> MarketProduct | None:
        """Verify an omitted product directly before treating it as absent or disabled."""
        catalog = await self.catalog_snapshot()
        found = next((p for p in catalog.products if p.product_id == product_id), None)
        if found is None and isinstance(self._provider, ProductLookupProvider):
            async with self._catalog_lock:
                # A concurrent watch may already have verified and added this product.
                found = (
                    next((p for p in self._catalog.products if p.product_id == product_id), None)
                    if self._catalog
                    else None
                )
                if found is None:
                    try:
                        found = await self._provider.get_product(product_id)
                    except MarketProductNotFoundError:
                        return None
                    if found.product_id != product_id:
                        raise ValueError("Provider returned a different product identity.")
                    current = self._catalog or catalog
                    self._set_catalog((*current.products, found), observed_at=current.observed_at)
        if (
            found is None
            or not found.trading_enabled
            or found.quote_currency not in SPOT_QUOTE_CURRENCIES
        ):
            return None
        return found

    async def instrument_catalog(self) -> InstrumentCatalog:
        """Return spot and futures instruments; each keeps its own fingerprint (ADR 0126)."""
        spot = await self.catalog_snapshot()
        futures = None if self.futures_catalog is None else await self.futures_catalog.snapshot()
        return build_instrument_catalog(spot, futures)

    async def enabled_instrument(self, product_id: str) -> Instrument | None:
        """Return one enabled spot or futures instrument, or ``None`` when absent/disabled.

        Spot ids take the existing spot path unchanged. A futures id without a configured
        futures provider is ``None`` (unknown, never assumed enabled).
        """
        if not is_futures_product_id(product_id):
            spot = await self.enabled_spot_product(product_id)
            if spot is None:
                return None
            return Instrument(product_id=spot.product_id, kind=InstrumentKind.SPOT, spot=spot)
        if self.futures_catalog is None:
            return None
        snapshot = await self.futures_catalog.snapshot()
        found = next((p for p in snapshot.products if p.product_id == product_id), None)
        if found is None or not found.trading_enabled:
            return None
        return Instrument(product_id=found.product_id, kind=found.kind, future=found)

    def _set_catalog(
        self, products: tuple[MarketProduct, ...], *, observed_at: datetime | None = None
    ) -> None:
        """Identify a normalized observation without converting its exact decimals to floats."""
        ordered = tuple(sorted(products, key=lambda p: p.product_id))
        canonical = json.dumps(
            [asdict(p) for p in ordered], default=str, sort_keys=True, separators=(",", ":")
        )
        self._catalog = ProductCatalogSnapshot(
            ordered,
            observed_at or datetime.now(UTC),
            "sha256:" + sha256(canonical.encode()).hexdigest(),
        )
        if observed_at is None:
            self._catalog_expires_at = time.monotonic() + 30.0

    async def get_preview(self, product_id: str, interval: CandleInterval) -> MarketDataPreview:
        """Return one selected product's read-only preview at a UTC instant."""
        return await self._provider.get_recent_preview(product_id, interval, datetime.now(UTC))

    async def get_hourly_preview(self, product_id: str) -> MarketDataPreview:
        """Return one selected product's read-only hourly preview at a UTC instant."""
        return await self.get_preview(product_id, CandleInterval.ONE_HOUR)

    async def get_recent_hourly_range(self, product_id: str) -> CandleRangeReport:
        """Return a seven-day closed hourly range for dashboard completeness diagnostics."""
        ends_at = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        return await self.get_hourly_range(
            product_id, ends_at - timedelta(days=7), ends_at, ends_at
        )

    async def get_range(
        self,
        product_id: str,
        interval: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Return one provider-neutral explicit range for internal ingestion."""
        provider = cast("HistoricalMarketDataProvider", self._provider)
        return await provider.get_historical_range(
            product_id,
            interval,
            starts_at,
            ends_at,
            now,
        )

    async def get_hourly_range(
        self,
        product_id: str,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Return one provider-neutral explicit hourly range for internal ingestion."""
        return await self.get_range(
            product_id,
            CandleInterval.ONE_HOUR,
            starts_at,
            ends_at,
            now,
        )
