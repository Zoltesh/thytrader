"""Deploy-anchored closed candle-window loading shared by execution services.

The execution worker's cycle and execution services outside it load closed bars through
these loaders, so every caller sees the same deploy-anchored window for one deployment
(ADR 0113).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from thytrader.evaluation.models import warmup_starts_at
from thytrader.market_data.models import parse_candle_interval
from thytrader.trading.geometry import entry_bar_bucket

if TYPE_CHECKING:
    from thytrader.market_data.models import Candle, CandleRangeReport, MarketProduct
    from thytrader.market_data.service import MarketDataService
    from thytrader.strategies.models import StrategyDefinition


async def _closed_window(
    market_data: MarketDataService,
    strategy: StrategyDefinition,
    *,
    deploy_anchor: datetime,
    as_of_closed_start: datetime | None = None,
) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
    """Fetch deploy-anchored warmup through the latest fully closed bar."""
    return await _closed_window_for(
        market_data,
        product_id=strategy.instrument.product_id,
        timeframe=strategy.timeframe,
        warmup_bars=strategy.data_requirements.warmup_bars,
        deploy_anchor=deploy_anchor,
        as_of_closed_start=as_of_closed_start,
    )


async def _closed_window_for(
    market_data: MarketDataService,
    *,
    product_id: str,
    timeframe: str,
    warmup_bars: int,
    deploy_anchor: datetime,
    as_of_closed_start: datetime | None = None,
) -> tuple[MarketProduct, tuple[Candle, ...], datetime]:
    """Fetch deploy-anchored warmup through one closed bar on an interval.

    The service's deploy-window cache (ADR 0113) segments settled history below the
    adapter's per-request bound and refetches the newest/unsettled tail with overlap.
    The start never slides: seeded indicators still consume their exact canonical
    deploy-prefix. Frozen history ignores provider corrections within this service
    generation; restart/eviction re-observes history from the same boundary. Budget
    exhaustion raises WindowCacheWarmingError, not an empty/gapped data verdict:
    consumers retry without changing lifecycle choices or evaluating a partial seed.
    Retained history and full indicator compute still grow with deployment lifetime.

    Coinbase returns no candle for an interval without trades. A bar missing between two
    real candles, and still missing on one re-fetch, is a confirmed no-trade interval and
    becomes a flat zero-volume bar, exactly as research datasets publish it (ADR 0095).
    A missing newest bar is never filled; decision clocks may wait for ADR 0104's
    bounded publication window before pausing. Required filter clocks remain fail-closed.
    """
    now = datetime.now(UTC)
    interval = parse_candle_interval(timeframe)
    last_closed_end = interval.align_closed_end(now)
    last_closed_start = (
        as_of_closed_start
        if as_of_closed_start is not None
        else last_closed_end - interval.duration
    )
    deploy_anchor_bar = entry_bar_bucket(deploy_anchor, timeframe)
    starts_at = warmup_starts_at(deploy_anchor_bar, warmup_bars, timeframe)
    preview = await market_data.get_preview(product_id, interval)

    async def fetch_range(starts_at: datetime, ends_at: datetime) -> CandleRangeReport:
        """Fetch one bounded segment at this cycle's immutable observation instant."""
        return await market_data.get_range(product_id, interval, starts_at, ends_at, now)

    candles = await market_data.window_cache.closed_window(
        fetch_range,
        product_id=product_id,
        interval=interval,
        starts_at=starts_at,
        last_closed_start=last_closed_start,
        now=now,
    )
    return preview.product, candles, last_closed_start
