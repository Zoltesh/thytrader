"""The latest closed, fresh candle of one product: the mark on-demand actions use.

Discretionary entries validate stop and take-profit against it, and inventory adoption
(ADR 0124) takes ownership of held coins at its close. A product that is not tradable, a
missing candle, or stale data is refused rather than replaced with a guessed price.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution.freshness import entry_prerequisites
from thytrader.market_data.models import parse_candle_interval
from thytrader.risk.models import RiskDecision
from thytrader.trading.ids import utc_now
from thytrader.trading.models import ExecutionConflictError

if TYPE_CHECKING:
    from thytrader.market_data.models import Candle, MarketProduct
    from thytrader.market_data.service import MarketDataService


async def closed_mark_context(
    market_data: MarketDataService, *, product_id: str, timeframe: str
) -> tuple[MarketProduct, Candle]:
    """Load product increments and the latest closed candle, refusing stale data.

    Raises:
        ExecutionConflictError: The product is not tradable, no closed candle exists, or
            the candle fails the entry freshness prerequisites.
    """
    interval = parse_candle_interval(timeframe)
    preview = await market_data.get_preview(product_id, interval)
    if preview.product.product_id != product_id or not preview.product.trading_enabled:
        raise ExecutionConflictError("Product is not a tradable USD spot market.")
    candles = preview.quality.candles
    if not candles:
        raise ExecutionConflictError("A closed mark candle is required before placing an order.")
    mark_candle = candles[-1]
    verdict = entry_prerequisites(
        product=preview.product,
        candle=mark_candle,
        now=utc_now(),
        timeframe=timeframe,
    )
    if verdict.decision is RiskDecision.DENY:
        raise ExecutionConflictError(verdict.detail)
    return preview.product, mark_candle
