"""Venue-history fakes for start-time history checks (ADR 0140).

``ListedHistory`` serves the demo's deterministic candles for any product id, futures
contracts included, from an optional listing instant onward: before it the venue has no
candles, as for a market younger than a strategy's warmup. Bars in ``quiet`` had no trades,
so the venue returns no candle for them either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.market_data.demo import DemoMarketData, _candle
from thytrader.market_data.quality import analyze_range
from thytrader.market_data.service import MarketDataService

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.market_data.models import CandleInterval, CandleRangeReport


class ListedHistory(DemoMarketData):
    """Demo candles for every product, none before ``listed_at`` or on a quiet bar."""

    def __init__(
        self, *, listed_at: datetime | None = None, quiet: frozenset[datetime] = frozenset()
    ) -> None:
        """Record the listing instant (None: history reaches back indefinitely)."""
        super().__init__()
        self.listed_at = listed_at
        self.quiet = quiet

    async def get_historical_range(
        self,
        product_id: str,
        interval: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Return the range's demo candles at or after the listing."""
        del product_id
        count = (ends_at - starts_at) // interval.duration
        bars = (starts_at + interval.duration * offset for offset in range(count))
        candles = tuple(
            _candle(bar)
            for bar in bars
            if (self.listed_at is None or bar >= self.listed_at) and bar not in self.quiet
        )
        return analyze_range(candles, interval, starts_at, ends_at, now)


def listed_history(
    *, listed_at: datetime | None = None, quiet: frozenset[datetime] = frozenset()
) -> MarketDataService:
    """A market-data service over :class:`ListedHistory`."""
    return MarketDataService(ListedHistory(listed_at=listed_at, quiet=quiet))
