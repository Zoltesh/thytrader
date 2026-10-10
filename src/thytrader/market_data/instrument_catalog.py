"""Futures listing cache and the combined spot-plus-futures instrument catalog (ADR 0126).

The spot ``ProductCatalogSnapshot`` and its fingerprint are untouched: the futures
listing is cached and fingerprinted on its own, and ``InstrumentCatalog`` carries both
fingerprints side by side instead of hashing them together.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
import json
import time
from typing import TYPE_CHECKING

from thytrader.market_data.instruments import (
    FuturesCatalogSnapshot,
    Instrument,
    InstrumentCatalog,
    InstrumentKind,
)

if TYPE_CHECKING:
    from thytrader.market_data.instruments import FuturesCatalogProvider, FuturesProduct
    from thytrader.market_data.service import ProductCatalogSnapshot

_FUTURES_CATALOG_TTL_SECONDS = 30.0


def futures_catalog_fingerprint(products: tuple[FuturesProduct, ...]) -> str:
    """Identify one futures listing by its exact normalized content.

    Decimals, instants and enums serialize through ``str`` so no value passes through
    binary floating point.
    """
    ordered = sorted(products, key=lambda product: product.product_id)
    canonical = json.dumps(
        [asdict(product) for product in ordered],
        default=str,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + sha256(canonical.encode()).hexdigest()


class FuturesCatalogCache:
    """Share one 30-second futures listing observation; concurrent misses fetch once."""

    def __init__(self, provider: FuturesCatalogProvider) -> None:
        """Bind the cache to one read-only futures listing provider."""
        self._provider = provider
        self._lock = asyncio.Lock()
        self._snapshot: FuturesCatalogSnapshot | None = None
        self._expires_at = 0.0

    async def snapshot(self) -> FuturesCatalogSnapshot:
        """Return the cached listing or read a fresh complete one; failures propagate."""
        async with self._lock:
            if self._snapshot is None or time.monotonic() >= self._expires_at:
                products = await self._provider.list_futures_products()
                if not products:
                    raise ValueError("The provider returned an empty futures listing.")
                ordered = tuple(sorted(products, key=lambda product: product.product_id))
                self._snapshot = FuturesCatalogSnapshot(
                    products=ordered,
                    observed_at=datetime.now(UTC),
                    fingerprint=futures_catalog_fingerprint(ordered),
                )
                self._expires_at = time.monotonic() + _FUTURES_CATALOG_TTL_SECONDS
            return self._snapshot


def build_instrument_catalog(
    spot: ProductCatalogSnapshot, futures: FuturesCatalogSnapshot | None
) -> InstrumentCatalog:
    """Combine one spot and one futures observation without merging their fingerprints."""
    instruments = [
        Instrument(product_id=product.product_id, kind=InstrumentKind.SPOT, spot=product)
        for product in spot.products
    ]
    if futures is not None:
        instruments.extend(
            Instrument(product_id=product.product_id, kind=product.kind, future=product)
            for product in futures.products
        )
    observed_at = spot.observed_at
    if futures is not None:
        observed_at = min(observed_at, futures.observed_at)
    return InstrumentCatalog(
        instruments=tuple(sorted(instruments, key=lambda item: item.product_id)),
        spot_fingerprint=spot.fingerprint,
        futures_fingerprint=None if futures is None else futures.fingerprint,
        observed_at=observed_at,
    )
