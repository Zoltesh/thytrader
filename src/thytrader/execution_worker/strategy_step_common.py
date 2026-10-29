"""Bar inputs shared by single-instrument and lockstep strategy evaluation.

Decides whether the newest recovered due bar may still enter, and prices last-close
marks for every held product so mode-wide daily-loss checks can fail closed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.execution.freshness import signal_still_valid
from thytrader.execution.inventory_marks import inventory_marks
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
    current_product_id: str,
    current_close: Decimal,
) -> dict[str, Decimal]:
    """Last-close marks for held products so mode-wide daily-loss can fail closed.

    Only inventory needs a mark: a flat or stopped book's loss is already realized. A failed
    read leaves its product unmarked, so admission treats that book's loss as unknown instead
    of this unrelated bar failing (ADR 0153).
    """
    marks: dict[str, Decimal] = {current_product_id: current_close}
    for product_id, close in (await inventory_marks(market_data, portfolio)).items():
        marks.setdefault(product_id, close)
    return marks
