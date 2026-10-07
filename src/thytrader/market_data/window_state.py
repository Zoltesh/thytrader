"""Shared transient state for bounded execution-window rebuilding."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.market_data.models import CandleInterval


class WindowCacheWarmingError(RuntimeError):
    """A scan budget was exhausted, not evidence of missing exchange data.

    Consumers retry without evaluating a partial prefix, advancing the decision cursor,
    changing lifecycle status, or skipping candle-independent order reconciliation.
    """

    def __init__(
        self,
        *,
        product_id: str,
        interval: CandleInterval,
        starts_at: datetime,
        scanned_through: datetime,
        requested_end: datetime,
        range_requests: int,
    ) -> None:
        """Expose precise rebuild progress without provider payloads or secrets."""
        super().__init__("Deploy-anchored candle history is warming within its request budget.")
        self.product_id = product_id
        self.interval = interval
        self.starts_at = starts_at
        self.scanned_through = scanned_through
        self.requested_end = requested_end
        self.range_requests = range_requests
