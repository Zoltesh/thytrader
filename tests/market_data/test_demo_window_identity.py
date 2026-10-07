"""Demo candle identity must survive segmentation just like real provider candle identity."""

from datetime import UTC, datetime, timedelta

import pytest

from thytrader.market_data import window_cache
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.models import Candle, CandleInterval, CandleRangeReport
from thytrader.market_data.window_cache import DeployWindowCache
from thytrader.market_data.window_state import WindowCacheWarmingError

pytestmark = pytest.mark.anyio


async def test_demo_range_preview_and_segmented_execution_share_timestamp_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A demo bar's synthetic OHLCV is independent of the range/page requesting it."""
    monkeypatch.setattr(window_cache, "RANGE_BLOCK_INTERVALS", 7)
    provider = DemoMarketData()
    interval = CandleInterval.ONE_HOUR
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + 80 * interval.duration
    now = end + timedelta(minutes=3)
    whole = await provider.get_historical_range("BTC-USD", interval, start, end, now)
    left = await provider.get_historical_range(
        "BTC-USD", interval, start, end - 10 * interval.duration, now
    )
    right = await provider.get_historical_range(
        "BTC-USD", interval, end - 10 * interval.duration, end, now
    )
    assert (*left.quality.candles, *right.quality.candles) == whole.quality.candles
    preview = await provider.get_recent_preview("BTC-USD", interval, now)
    assert preview.quality.candles == whole.quality.candles[-24:]

    async def fetch(starts_at: datetime, ends_at: datetime) -> CandleRangeReport:
        """Supply the same observation time to every bounded page."""
        return await provider.get_historical_range("BTC-USD", interval, starts_at, ends_at, now)

    cache = DeployWindowCache()
    result: tuple[Candle, ...] = ()
    for _attempt in range(4):
        try:
            result = await cache.closed_window(
                fetch,
                product_id="BTC-USD",
                interval=interval,
                starts_at=start,
                last_closed_start=end - interval.duration,
                now=now,
            )
        except WindowCacheWarmingError:
            continue
        break
    assert result == whole.quality.candles
