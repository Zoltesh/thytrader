"""When a missing candle stops being unsettled and becomes a confirmed provider hole.

Market-data coverage checks and the market-data worker share this rule, so it lives
in the market_data package. ``thytrader.market_data_worker.pages`` re-exports it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from thytrader.market_data.models import CandleInterval

# Coinbase can publish the newest candles a little late. A missing bar newer than this
# window (or one bar, whichever is longer) is unsettled: the worker waits for it instead
# of treating it as a permanent provider hole.
HOLE_SETTLE_MINIMUM = timedelta(minutes=15)


def settle_cutoff(closed_end: datetime, interval: CandleInterval) -> datetime:
    """Return the instant from which a missing bar is not yet a confirmed hole."""
    window = interval.duration if interval.duration > HOLE_SETTLE_MINIMUM else HOLE_SETTLE_MINIMUM
    return closed_end - window
