"""Provider pages split into gap-free runs; missing bars are never repaired."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from thytrader.market_data.models import Candle, CandleInterval, CandleRangeReport
from thytrader.market_data.quality import analyze_range
from thytrader.market_data_worker.pages import settle_cutoff, split_page

_HOUR = CandleInterval.ONE_HOUR
_START = datetime(2026, 7, 1, tzinfo=UTC)
_END = _START + timedelta(hours=10)


def _report(
    starts_at: datetime,
    ends_at: datetime,
    interval: CandleInterval = _HOUR,
    *,
    missing: frozenset[datetime] = frozenset(),
) -> CandleRangeReport:
    """Build one validated provider report for ``[starts_at, ends_at)`` without ``missing``."""
    count = int((ends_at - starts_at) / interval.duration)
    candles = tuple(
        Candle(
            starts_at=starts_at + interval.duration * index,
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("90"),
            close=Decimal("105"),
            volume=Decimal("1"),
        )
        for index in range(count)
        if starts_at + interval.duration * index not in missing
    )
    return analyze_range(candles, interval, starts_at, ends_at, ends_at)


def test_complete_page_is_one_run_that_needs_no_confirmation() -> None:
    """A fully present page is published as one run without a confirmation re-fetch."""
    page = split_page(_report(_START, _END), _START, _END, _HOUR)

    assert page.complete is True
    assert len(page.runs) == 1
    assert len(page.runs[0]) == 10
    assert page.gaps() == ()
    assert page.tail_run() == page.runs[0]
    assert page.needs_confirmation(settle_cutoff(_END, _HOUR)) is False


def test_page_splits_at_every_missing_bar() -> None:
    """Interior holes end runs and are reported as half-open missing spans."""
    holes = frozenset({_START + timedelta(hours=3), _START + timedelta(hours=4)})
    page = split_page(_report(_START, _END, missing=holes), _START, _END, _HOUR)

    assert page.complete is False
    assert page.consistent is True
    assert [(run[0].starts_at, len(run)) for run in page.runs] == [
        (_START, 3),
        (_START + timedelta(hours=5), 5),
    ]
    assert page.gaps() == ((_START + timedelta(hours=3), _START + timedelta(hours=5)),)
    assert page.tail_run() == page.runs[1]
    assert page.has_settled_hole(settle_cutoff(_END, _HOUR)) is True


def test_newest_bar_missing_is_an_unsettled_gap() -> None:
    """A missing newest bar may still be published late, so it is not a confirmed hole."""
    newest = _END - timedelta(hours=1)
    page = split_page(_report(_START, _END, missing=frozenset({newest})), _START, _END, _HOUR)
    cutoff = settle_cutoff(_END, _HOUR)

    assert cutoff == newest
    assert page.tail_run() is None
    assert page.has_settled_hole(cutoff) is False
    assert page.needs_confirmation(cutoff) is False
    assert page.first_unsettled_missing(cutoff) == newest
    run = page.newest_run_ending_by(newest)
    assert run is not None
    assert run[-1].starts_at == newest - timedelta(hours=1)


def test_one_minute_settle_window_spans_fifteen_bars() -> None:
    """Fast clocks keep a fifteen-minute settle window; slow clocks keep one bar."""
    closed_end = datetime(2026, 7, 1, 12, tzinfo=UTC)

    assert settle_cutoff(closed_end, CandleInterval.ONE_MINUTE) == closed_end - timedelta(
        minutes=15
    )
    assert settle_cutoff(closed_end, CandleInterval.FIVE_MINUTES) == closed_end - timedelta(
        minutes=15
    )
    assert settle_cutoff(closed_end, CandleInterval.ONE_DAY) == closed_end - timedelta(days=1)


def test_report_flagged_incomplete_with_every_bar_present_yields_no_runs() -> None:
    """An internally inconsistent report fails closed: nothing from it may be published."""
    forged = replace(_report(_START, _END), complete=False)
    page = split_page(forged, _START, _END, _HOUR)

    assert page.consistent is False
    assert page.runs == ()
    assert page.gaps() == ((_START, _END),)
    assert page.needs_confirmation(settle_cutoff(_END, _HOUR)) is True


def test_report_for_other_bounds_yields_no_runs() -> None:
    """A report that answers a different window cannot seed or extend an island."""
    other = _report(_START + timedelta(hours=1), _END)
    page = split_page(other, _START, _END, _HOUR)

    assert page.consistent is False
    assert page.runs == ()


def test_off_grid_candles_yield_no_runs() -> None:
    """Candles that are not on the page's interval grid are rejected, not realigned."""
    shifted = _report(_START + timedelta(minutes=30), _END + timedelta(minutes=30))
    off_grid = replace(
        shifted,
        starts_at=_START,
        ends_at=_END,
        complete=False,
        quality=replace(shifted.quality, candles=shifted.quality.candles[:-1]),
    )
    page = split_page(off_grid, _START, _END, _HOUR)

    assert page.consistent is False
    assert page.runs == ()
