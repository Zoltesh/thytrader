"""Split provider candle pages into gap-free runs and confirm their missing bars.

A page is one provider request of at most ``HISTORICAL_REQUEST_MAX_CANDLES`` bars on the
interval grid. Nothing here fabricates a candle: the worker decides which confirmed
missing bars are no-trade intervals (ADR 0095). A page and its confirmation re-fetch are
merged so that a bar counts as missing only when both responses omit it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from itertools import pairwise
from typing import TYPE_CHECKING

from thytrader.market_data.hole_settlement import HOLE_SETTLE_MINIMUM, settle_cutoff

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from thytrader.market_data.models import Candle, CandleInterval, CandleRangeReport

__all__ = [
    "HOLE_SETTLE_MINIMUM",
    "CandlePage",
    "CandleRun",
    "merge_confirmed_pages",
    "run_end",
    "settle_cutoff",
    "split_page",
]

type CandleRun = tuple[Candle, ...]


def run_end(run: Sequence[Candle], interval: CandleInterval) -> datetime:
    """Return the exclusive end of one non-empty gap-free run."""
    return run[-1].starts_at + interval.duration


@dataclass(frozen=True, slots=True)
class CandlePage:
    """One provider page split into maximal gap-free runs, oldest first.

    ``consistent`` is False when the report's bounds, grid alignment, ordering, or
    completeness flag disagree with its candles. Such a page contributes no runs, so
    every bar in it counts as missing (fail closed).
    """

    starts_at: datetime
    ends_at: datetime
    interval: CandleInterval
    runs: tuple[CandleRun, ...]
    complete: bool
    consistent: bool

    def gaps(self) -> tuple[tuple[datetime, datetime], ...]:
        """Return the missing half-open spans before, between, and after the runs."""
        spans: list[tuple[datetime, datetime]] = []
        expected = self.starts_at
        for run in self.runs:
            if run[0].starts_at > expected:
                spans.append((expected, run[0].starts_at))
            expected = run_end(run, self.interval)
        if expected < self.ends_at:
            spans.append((expected, self.ends_at))
        return tuple(spans)

    def has_settled_hole(self, cutoff: datetime) -> bool:
        """True when some missing bar starts before ``cutoff`` and is therefore final."""
        return any(gap_start < cutoff for gap_start, _ in self.gaps())

    def needs_confirmation(self, cutoff: datetime) -> bool:
        """True when acting on this page would record a hole that a re-fetch should confirm."""
        return not self.complete and (not self.consistent or self.has_settled_hole(cutoff))

    def first_unsettled_missing(self, cutoff: datetime) -> datetime | None:
        """Return the earliest missing bar at or after ``cutoff``, if any."""
        for gap_start, gap_end in self.gaps():
            candidate = gap_start if gap_start >= cutoff else cutoff
            if candidate < gap_end:
                return candidate
        return None

    def candles(self) -> tuple[Candle, ...]:
        """Return every candle on the page, oldest first."""
        return tuple(candle for run in self.runs for candle in run)


def split_page(
    report: CandleRangeReport,
    starts_at: datetime,
    ends_at: datetime,
    interval: CandleInterval,
) -> CandlePage:
    """Split one provider report for ``[starts_at, ends_at)`` into gap-free runs.

    A report that matches the request completely is one run. An incomplete report is
    split at each missing bar. A report that is inconsistent with its own request (wrong
    bounds, off-grid or unordered candles, or flagged incomplete with nothing missing)
    yields no runs at all.
    """
    requested = (ends_at - starts_at) // interval.duration
    candles = report.quality.candles
    if _is_complete_match(report, starts_at, ends_at):
        return CandlePage(starts_at, ends_at, interval, (candles,), complete=True, consistent=True)
    consistent = (
        report.starts_at == starts_at
        and report.ends_at == ends_at
        and len(candles) < requested
        and _on_grid(candles, starts_at, ends_at, interval)
    )
    runs = _gap_free_runs(candles, interval) if consistent else ()
    return CandlePage(starts_at, ends_at, interval, runs, complete=False, consistent=consistent)


def merge_confirmed_pages(first: CandlePage, confirmation: CandlePage) -> CandlePage:
    """Combine a page with its re-fetch: a bar is missing only when both responses omit it.

    The confirmation's candle wins where both carry a bar. An inconsistent confirmation
    yields an inconsistent page (fail closed); an inconsistent first response contributes
    nothing.
    """
    if not confirmation.consistent or not first.consistent:
        return confirmation
    by_start = {candle.starts_at: candle for candle in first.candles()}
    by_start.update({candle.starts_at: candle for candle in confirmation.candles()})
    candles = tuple(by_start[start] for start in sorted(by_start))
    requested = (confirmation.ends_at - confirmation.starts_at) // confirmation.interval.duration
    return CandlePage(
        confirmation.starts_at,
        confirmation.ends_at,
        confirmation.interval,
        _gap_free_runs(candles, confirmation.interval),
        complete=len(candles) == requested,
        consistent=True,
    )


def _is_complete_match(report: CandleRangeReport, starts_at: datetime, ends_at: datetime) -> bool:
    """True when the report covers exactly the requested range with every bar present."""
    return (
        report.complete
        and report.starts_at == starts_at
        and report.ends_at == ends_at
        and report.requested_candle_count == report.quality.candle_count
        and report.quality.gap_count == 0
        and report.quality.missing_intervals == 0
        and len(report.quality.candles) == report.requested_candle_count
    )


def _on_grid(
    candles: tuple[Candle, ...],
    starts_at: datetime,
    ends_at: datetime,
    interval: CandleInterval,
) -> bool:
    """Require strictly increasing candles inside the page and aligned to its grid."""
    zero = timedelta(0)
    return all(
        starts_at <= candle.starts_at < ends_at
        and (candle.starts_at - starts_at) % interval.duration == zero
        for candle in candles
    ) and all(later.starts_at > earlier.starts_at for earlier, later in pairwise(candles))


def _gap_free_runs(candles: tuple[Candle, ...], interval: CandleInterval) -> tuple[CandleRun, ...]:
    """Split ordered on-grid candles wherever consecutive starts skip a bar."""
    runs: list[CandleRun] = []
    current: list[Candle] = []
    for candle in candles:
        if current and candle.starts_at != current[-1].starts_at + interval.duration:
            runs.append(tuple(current))
            current = []
        current.append(candle)
    if current:
        runs.append(tuple(current))
    return tuple(runs)
