"""Desk market rows: stale evidence is unknown, and returns are measured in hours, not bars."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from thytrader.agent_desk.market import summarize
from thytrader.market_data.models import Candle

_NEWEST = datetime(2026, 10, 20, 12, tzinfo=UTC)


def _candle(hours_ago: int, close: str) -> Candle:
    """One flat hourly candle ``hours_ago`` before the newest expected bar."""
    price = Decimal(close)
    return Candle(
        starts_at=_NEWEST - timedelta(hours=hours_ago),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=Decimal(1),
    )


def test_a_stale_newest_bar_leaves_the_product_without_a_price() -> None:
    """Six-hour-old data is not a mark: no price, and the reason says why."""
    row = summarize("BTC-USD", [_candle(7, "100"), _candle(6, "101")], newest_expected=_NEWEST)
    assert row.last_close is None
    assert row.unavailable_reason is not None
    assert row.unavailable_reason.startswith("stale")


def test_returns_use_the_bar_that_many_hours_ago_and_skip_gaps() -> None:
    """With hours 2-3 missing, the 4h return is unknown rather than a 2-bar return."""
    row = summarize(
        "BTC-USD",
        [_candle(4, "100"), _candle(1, "110"), _candle(0, "121")],
        newest_expected=_NEWEST,
    )
    assert row.last_close == Decimal(121)
    assert row.return_1h == Decimal("0.1")
    assert row.return_4h == Decimal("0.21")
    gapped = summarize("BTC-USD", [_candle(5, "100"), _candle(0, "121")], newest_expected=_NEWEST)
    assert gapped.return_4h is None
