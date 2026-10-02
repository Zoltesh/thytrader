"""Paper/live windows fill confirmed no-trade bars like research datasets (ADR 0095).

Live and paper read closed bars straight from the provider. A bar Coinbase omitted
between two traded bars, and still omits on one re-fetch, becomes the same flat
zero-volume bar the dataset carries, so the strategy sees the series its backtest saw.
A missing newest bar is never filled: the window still pauses the book as a data gap.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from thytrader.execution_worker.service import _closed_window_for, new_closed_bars
from thytrader.market_data.demo import DemoMarketData
from thytrader.market_data.models import CandleInterval, CandleRangeReport
from thytrader.market_data.quality import analyze_range
from thytrader.market_data.service import MarketDataService

if TYPE_CHECKING:
    from collections.abc import Callable

    from thytrader.market_data.models import Candle

_HOUR = CandleInterval.ONE_HOUR


class _QuietDemo(DemoMarketData):
    """Demo provider that omits chosen bars of each window, counting range calls."""

    def __init__(self, omit: Callable[[int, int, int], frozenset[int]]) -> None:
        """``omit(call, bars, offset)`` decides per call which window offsets had no trades."""
        self._omit = omit
        self.calls = 0

    async def get_historical_range(
        self,
        product_id: str,
        interval: CandleInterval,
        starts_at: datetime,
        ends_at: datetime,
        now: datetime,
    ) -> CandleRangeReport:
        """Serve the demo range without the omitted offsets."""
        report = await super().get_historical_range(product_id, interval, starts_at, ends_at, now)
        self.calls += 1
        bars = len(report.quality.candles)
        omitted = self._omit(self.calls, bars, 0)
        candles = tuple(
            candle for offset, candle in enumerate(report.quality.candles) if offset not in omitted
        )
        return analyze_range(candles, interval, starts_at, ends_at, now)


def _window(provider: _QuietDemo) -> tuple[tuple[Candle, ...], datetime]:
    """Load one 1h deploy-anchored window through the execution worker's loader."""
    _product, candles, last_closed = asyncio.run(
        _closed_window_for(
            MarketDataService(provider),
            product_id="BTC-USD",
            timeframe="1h",
            warmup_bars=12,
            deploy_anchor=datetime.now(UTC) - timedelta(hours=2),
        )
    )
    return candles, last_closed


def test_interior_no_trade_bars_are_filled_after_one_confirmation() -> None:
    """Two quiet hours between trades become flat bars; the book keeps evaluating."""
    provider = _QuietDemo(lambda _call, _bars, _offset: frozenset({3, 4}))

    candles, last_closed = _window(provider)

    assert provider.calls == 2, "one fetch plus one confirmation"
    flat = [candle for candle in candles if candle.volume == 0]
    assert len(flat) == 2
    assert all(candle.close == candles[2].close for candle in flat)
    assert all(candle.open == candle.high == candle.low == candle.close for candle in flat)
    due = new_closed_bars(
        candles,
        last_evaluated_bar=None,
        expected_last_start=last_closed,
        bar_duration=_HOUR.duration,
    )
    assert due is not None
    assert due[-1].starts_at == last_closed


def test_a_bar_present_in_either_response_is_real() -> None:
    """A transient omission in the first response is healed by the confirmation."""
    provider = _QuietDemo(
        lambda call, _bars, _offset: frozenset({3, 4}) if call == 1 else frozenset({4})
    )

    candles, _last_closed = _window(provider)

    assert [candle.volume == 0 for candle in candles[2:6]] == [False, False, True, False]


def test_a_liquid_window_is_fetched_once_and_unchanged() -> None:
    """Gap-free windows cost no confirmation and carry no flat bars."""
    provider = _QuietDemo(lambda _call, _bars, _offset: frozenset())

    candles, _last_closed = _window(provider)

    assert provider.calls == 1
    assert all(candle.volume > 0 for candle in candles)


def test_a_missing_newest_bar_still_pauses_as_a_data_gap() -> None:
    """The newest closed bar may still be published; it is never synthesized."""
    provider = _QuietDemo(lambda _call, bars, _offset: frozenset({bars - 1}))

    candles, last_closed = _window(provider)

    assert all(candle.volume > 0 for candle in candles)
    assert candles[-1].starts_at < last_closed
    assert (
        new_closed_bars(
            candles,
            last_evaluated_bar=None,
            expected_last_start=last_closed,
            bar_duration=_HOUR.duration,
        )
        is None
    )
