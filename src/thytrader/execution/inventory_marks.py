"""Last-close marks for every product a set of books holds (ADR 0129 fleet evidence).

Daily-loss evidence needs a mark for each held product. Fleet entry health and discretionary
entry admission both read them here, one market-data preview per product.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.market_data.models import parse_candle_interval
from thytrader.trading.models import resolved_product_id, snapshot_positions

if TYPE_CHECKING:
    from collections.abc import Sequence
    from decimal import Decimal

    from thytrader.market_data.service import MarketDataService
    from thytrader.trading.models import DeploymentSnapshot

_MARK_ERRORS = (RuntimeError, ValueError, TypeError, OSError, LookupError, AttributeError)


async def inventory_marks(
    market_data: MarketDataService | None, snapshots: Sequence[DeploymentSnapshot]
) -> dict[str, Decimal]:
    """Last-close marks for every product with open inventory; a failed read stays unmarked."""
    marks: dict[str, Decimal] = {}
    if market_data is None:
        return marks
    for snapshot in snapshots:
        timeframe = snapshot.deployment.timeframe or "1h"
        for position in snapshot_positions(snapshot):
            product_id = resolved_product_id(position.product_id, snapshot.deployment)
            if product_id in marks:
                continue
            try:
                preview = await market_data.get_preview(
                    product_id, parse_candle_interval(timeframe)
                )
            except _MARK_ERRORS:
                continue
            candles = preview.quality.candles
            if candles:
                marks[product_id] = candles[-1].close
    return marks
