"""Stitch scored out-of-sample equity curves into one compounded OOS path."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
)

if TYPE_CHECKING:
    from thytrader.backtest.models import BacktestResult


MAX_STITCHED_POINTS = 4096


class StitchedEquityPoint(BaseModel):
    """One compounded mark-to-market point on a stitched OOS path."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    candle_starts_at: datetime
    equity: str
    fold_index: int = Field(ge=0)
    result_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @field_serializer("candle_starts_at", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        """Serialize stitch marks with a canonical Z suffix."""
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class StitchedOosEquity(BaseModel):
    """Derived sequential OOS equity. Not a fourth backtest engine."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    available: bool
    reason: str | None = None
    initial_equity: str | None = None
    final_equity: str | None = None
    total_return_fraction: str | None = None
    maximum_drawdown: str | None = None
    maximum_drawdown_fraction: str | None = None
    point_count: int = Field(default=0, ge=0)
    points: tuple[StitchedEquityPoint, ...] = ()


@dataclass(frozen=True, slots=True)
class StitchSourceWindow:
    """One scored OOS child whose equity curve may participate in a stitch."""

    fold_index: int
    evaluation_start: datetime
    evaluation_end: datetime
    result_fingerprint: str
    result: BacktestResult


def stitch_oos_equity(windows: tuple[StitchSourceWindow, ...]) -> StitchedOosEquity:
    """Compound non-overlapping OOS equity returns without interpolating gaps."""
    if len(windows) < 2:
        return StitchedOosEquity(
            available=False,
            reason="Stitched OOS equity needs at least two scored out-of-sample windows.",
        )
    ordered = tuple(sorted(windows, key=lambda item: (item.evaluation_start, item.fold_index)))
    overlap = _first_overlap(ordered)
    if overlap is not None:
        return StitchedOosEquity(available=False, reason=overlap)
    tracker = _StitchTracker(initial=Decimal(ordered[0].result.summary.initial_equity))
    for window in ordered:
        window_initial = Decimal(window.result.summary.initial_equity)
        if window_initial == 0:
            return StitchedOosEquity(
                available=False,
                reason="Stitched OOS equity cannot scale a window whose initial equity is 0.",
            )
        for point in window.result.equity_curve:
            scaled = tracker.capital * (Decimal(point.equity) / window_initial)
            tracker.maybe_append(
                timestamp=point.candle_starts_at,
                equity=scaled,
                fold_index=window.fold_index,
                result_fingerprint=window.result_fingerprint,
            )
        tracker.capital = tracker.capital * (
            Decimal(window.result.summary.final_equity) / window_initial
        )
        tracker.maybe_append(
            timestamp=window.evaluation_end,
            equity=tracker.capital,
            fold_index=window.fold_index,
            result_fingerprint=window.result_fingerprint,
        )
    return tracker.document()


@dataclass
class _StitchTracker:
    """Accumulate compounded OOS marks and peak-to-trough drawdown."""

    initial: Decimal
    capital: Decimal = field(init=False)
    peak: Decimal = field(init=False)
    max_drawdown: Decimal = field(default_factory=lambda: Decimal(0))
    max_drawdown_fraction: Decimal = field(default_factory=lambda: Decimal(0))
    last_timestamp: datetime | None = None
    points: list[StitchedEquityPoint] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Start capital and peak at the first window's initial equity."""
        self.capital = self.initial
        self.peak = self.initial

    def maybe_append(
        self,
        *,
        timestamp: datetime,
        equity: Decimal,
        fold_index: int,
        result_fingerprint: str,
    ) -> None:
        """Record one mark when it is strictly after the previous timestamp."""
        if self.last_timestamp is not None and timestamp <= self.last_timestamp:
            return
        if equity > self.peak:
            self.peak = equity
        drawdown = self.peak - equity
        if drawdown > self.max_drawdown:
            self.max_drawdown = drawdown
        drawdown_fraction = Decimal(0) if self.peak == 0 else drawdown / self.peak
        if drawdown_fraction > self.max_drawdown_fraction:
            self.max_drawdown_fraction = drawdown_fraction
        self.points.append(
            StitchedEquityPoint(
                candle_starts_at=timestamp,
                equity=_canonical_decimal(equity),
                fold_index=fold_index,
                result_fingerprint=result_fingerprint,
            )
        )
        self.last_timestamp = timestamp

    def document(self) -> StitchedOosEquity:
        """Freeze the accumulated path, or explain why stitching is unavailable."""
        if not self.points:
            return StitchedOosEquity(
                available=False,
                reason="Stitched OOS equity has no mark-to-market points.",
            )
        final = self.capital
        published_points = tuple(self.points)
        if len(published_points) > MAX_STITCHED_POINTS:
            published_points = ()
        return_fraction = (
            _canonical_decimal((final - self.initial) / self.initial) if self.initial != 0 else "0"
        )
        return StitchedOosEquity(
            available=True,
            initial_equity=_canonical_decimal(self.initial),
            final_equity=_canonical_decimal(final),
            total_return_fraction=return_fraction,
            maximum_drawdown=_canonical_decimal(self.max_drawdown),
            maximum_drawdown_fraction=_canonical_decimal(self.max_drawdown_fraction),
            point_count=len(self.points),
            points=published_points,
        )


def unavailable_stitched_equity(reason: str) -> StitchedOosEquity:
    """Return an explicit unavailable stitch document."""
    return StitchedOosEquity(available=False, reason=reason)


def _first_overlap(windows: tuple[StitchSourceWindow, ...]) -> str | None:
    """Describe the first overlapping pair, if any."""
    previous = windows[0]
    for current in windows[1:]:
        if current.evaluation_start < previous.evaluation_end:
            return (
                "OOS windows overlap, so stitched equity is not a continuous path. "
                "Use equal-weight OOS aggregates instead."
            )
        previous = current
    return None


def _canonical_decimal(value: Decimal) -> str:
    """Render a finite Decimal as a plain string without scientific notation."""
    quantized = value.quantize(Decimal("0.000000000000000001"), rounding=ROUND_HALF_EVEN)
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


SUMMARY_STITCHED_POINTS = 200
"""Display cap for stitched OOS points in a study summary (the full study keeps them all)."""


def downsample_stitched_points(
    points: tuple[StitchedEquityPoint, ...], max_points: int = SUMMARY_STITCHED_POINTS
) -> tuple[StitchedEquityPoint, ...]:
    """Thin a stitched OOS path for display like the portfolio chart does.

    Keeps the first and last mark and, for each of ``(max_points - 2) // 2`` buckets, the
    lowest and highest equity mark in time order, so drawdowns stay visible. Display
    only: the canonical study keeps every mark and its fingerprint is unchanged.
    """
    if max_points < 4 or len(points) <= max_points:
        return points
    inner = points[1:-1]
    buckets = max(1, (max_points - 2) // 2)
    size = -(-len(inner) // buckets)
    kept: list[StitchedEquityPoint] = [points[0]]
    for start in range(0, len(inner), size):
        bucket = inner[start : start + size]
        low = min(bucket, key=lambda point: Decimal(point.equity))
        high = max(bucket, key=lambda point: Decimal(point.equity))
        unique = {low.candle_starts_at: low, high.candle_starts_at: high}
        kept.extend(sorted(unique.values(), key=lambda point: point.candle_starts_at))
    kept.append(points[-1])
    return tuple(kept)
