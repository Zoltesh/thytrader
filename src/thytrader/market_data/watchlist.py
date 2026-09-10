"""Durable watchlist of market-data ingestion targets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from thytrader.market_data.models import CandleInterval, parse_candle_interval


class MarketDataWatchlistUnavailableError(RuntimeError):
    """Signal that the ingestion watchlist store is unavailable."""


class MarketDataWatchlistError(ValueError):
    """Signal an invalid watchlist mutation."""


@dataclass(frozen=True, slots=True)
class MarketDataWatchTarget:
    """One provider/product/timeframe the worker and one-shot ingest should cover."""

    provider: str
    product_id: str
    timeframe: CandleInterval
    lookback_hours: int
    enabled: bool
    updated_at: datetime
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        """Reject empty identities and out-of-range lookbacks."""
        if not self.provider.strip() or not self.product_id.strip():
            raise MarketDataWatchlistError("Watch targets require a provider and product id.")
        if self.lookback_hours < 1 or self.lookback_hours > 2_160:
            raise MarketDataWatchlistError("Watch lookback_hours must be between 1 and 2160.")
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() != UTC.utcoffset(
            self.updated_at
        ):
            raise MarketDataWatchlistError("Watch timestamps must be timezone-aware UTC.")


@runtime_checkable
class MarketDataWatchlistStore(Protocol):
    """Persist the operator-managed ingestion watchlist."""

    async def list_all(self) -> tuple[MarketDataWatchTarget, ...]:
        """Return every watch target, including disabled rows."""
        ...

    async def list_enabled(self) -> tuple[MarketDataWatchTarget, ...]:
        """Return enabled ingestion targets in stable identity order."""
        ...

    async def get(
        self,
        provider: str,
        product_id: str,
        timeframe: CandleInterval,
    ) -> MarketDataWatchTarget | None:
        """Return one exact watch target when present."""
        ...

    async def upsert(self, target: MarketDataWatchTarget) -> MarketDataWatchTarget:
        """Insert or replace one watch target."""
        ...


class DisabledMarketDataWatchlistStore:
    """Reject watchlist access when PostgreSQL is not configured."""

    async def list_all(self) -> tuple[MarketDataWatchTarget, ...]:
        """Reject reads so a missing store never looks like an empty catalog."""
        raise MarketDataWatchlistUnavailableError("Market-data watchlist is unavailable.")

    async def list_enabled(self) -> tuple[MarketDataWatchTarget, ...]:
        """Reject reads so a missing store never looks like an empty catalog."""
        raise MarketDataWatchlistUnavailableError("Market-data watchlist is unavailable.")

    async def get(
        self,
        provider: str,
        product_id: str,
        timeframe: CandleInterval,
    ) -> MarketDataWatchTarget | None:
        """Reject reads so a missing store never looks like an empty catalog."""
        del provider, product_id, timeframe
        raise MarketDataWatchlistUnavailableError("Market-data watchlist is unavailable.")

    async def upsert(self, target: MarketDataWatchTarget) -> MarketDataWatchTarget:
        """Reject writes because durable state is unavailable."""
        del target
        raise MarketDataWatchlistUnavailableError("Market-data watchlist is unavailable.")


class InMemoryMarketDataWatchlistStore:
    """Deterministic watchlist used by tests and database-free API harnesses."""

    def __init__(self) -> None:
        """Initialize an empty identity mapping."""
        self._targets: dict[tuple[str, str, CandleInterval], MarketDataWatchTarget] = {}

    async def list_all(self) -> tuple[MarketDataWatchTarget, ...]:
        """Return every stored target in stable identity order."""
        return tuple(self._targets[key] for key in sorted(self._targets, key=_sort_key))

    async def list_enabled(self) -> tuple[MarketDataWatchTarget, ...]:
        """Return enabled targets in stable identity order."""
        return tuple(target for target in await self.list_all() if target.enabled)

    async def get(
        self,
        provider: str,
        product_id: str,
        timeframe: CandleInterval,
    ) -> MarketDataWatchTarget | None:
        """Return one exact watch target when present."""
        return self._targets.get((provider, product_id, timeframe))

    async def upsert(self, target: MarketDataWatchTarget) -> MarketDataWatchTarget:
        """Insert or replace one in-memory watch target."""
        key = (target.provider, target.product_id, target.timeframe)
        prior = self._targets.get(key)
        stored = MarketDataWatchTarget(
            provider=target.provider,
            product_id=target.product_id,
            timeframe=target.timeframe,
            lookback_hours=target.lookback_hours,
            enabled=target.enabled,
            updated_at=target.updated_at,
            created_at=prior.created_at if prior is not None else target.updated_at,
        )
        self._targets[key] = stored
        return stored


def parse_watch_timeframe(value: str) -> CandleInterval:
    """Parse 1h or 5m watchlist timeframes."""
    try:
        return parse_candle_interval(value)
    except ValueError as error:
        raise MarketDataWatchlistError(str(error)) from error


async def ensure_default_watch_target(
    store: MarketDataWatchlistStore,
    *,
    provider: str,
    product_id: str,
    lookback_hours: int,
    now: datetime,
) -> None:
    """Seed BTC-USD 1h (or the configured product) when the watchlist is empty."""
    existing = await store.list_all()
    if existing:
        return
    await store.upsert(
        MarketDataWatchTarget(
            provider=provider,
            product_id=product_id,
            timeframe=CandleInterval.ONE_HOUR,
            lookback_hours=lookback_hours,
            enabled=True,
            updated_at=now.astimezone(UTC),
        )
    )


def _sort_key(
    key: tuple[str, str, CandleInterval],
) -> tuple[str, str, str]:
    """Order watch identities by provider, product, then timeframe token."""
    provider, product_id, timeframe = key
    return (provider, product_id, timeframe.value)
