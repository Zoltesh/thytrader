"""Bar inputs shared by single-instrument and lockstep strategy evaluation.

Decides whether the newest recovered due bar may still enter, and prices last-close
marks for every occupied product so mode-wide daily-loss checks can fail closed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution.freshness import signal_still_valid
from thytrader.market_data.models import parse_candle_interval
from thytrader.trading.ids import utc_now

if TYPE_CHECKING:
    from collections.abc import Sequence
    from decimal import Decimal

    from thytrader.market_data.models import Candle
    from thytrader.market_data.service import MarketDataService
    from thytrader.trading.models import DeploymentSnapshot


def _latest_due_bar_may_enter(candle: Candle, *, timeframe: str, is_latest: bool) -> bool:
    """True when this recovered close is the newest due bar and still within max age."""
    return is_latest and signal_still_valid(
        candle=candle,
        timeframe=timeframe,
        now=utc_now(),
        current_quote=candle.close,
    )


async def _portfolio_marks(
    market_data: MarketDataService,
    *,
    portfolio: Sequence[DeploymentSnapshot],
    fallback_timeframe: str,
    current_product_id: str,
    current_close: Decimal,
) -> dict[str, Decimal]:
    """Last-close marks for occupied products so mode-wide daily-loss can fail closed."""
    marks: dict[str, Decimal] = {current_product_id: current_close}
    for snapshot in portfolio:
        timeframe = snapshot.deployment.timeframe or fallback_timeframe
        product_ids = {snapshot.deployment.product_id}
        for runtime in snapshot.instrument_runtimes:
            product_ids.add(runtime.product_id)
        for position in snapshot.positions:
            if position.product_id:
                product_ids.add(position.product_id)
        for product_id in product_ids:
            if product_id in marks:
                continue
            close = await _last_close(market_data, product_id=product_id, timeframe=timeframe)
            if close is not None:
                marks[product_id] = close
    return marks


async def _last_close(
    market_data: MarketDataService, *, product_id: str, timeframe: str
) -> Decimal | None:
    """Return the latest complete close, or None when that window is empty."""
    preview = await market_data.get_preview(product_id, parse_candle_interval(timeframe))
    candles = preview.quality.candles
    if not candles:
        return None
    return candles[-1].close
