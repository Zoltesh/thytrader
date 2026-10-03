"""Bound a newest-bar publication wait to its absolute UTC close (ADR 0104)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.market_data.models import Candle

NEWEST_BAR_SETTLE_SECONDS = 120


def newest_bar_settling(
    candles: Sequence[Candle],
    *,
    expected_last_start: datetime,
    bar_duration: timedelta,
    now: datetime | None = None,
) -> bool:
    """Whether exactly one newest bar is absent, with contiguous history and time left.

    The deadline is the missing bar's close plus two minutes, never a process-local
    timer. Restarting cannot extend it. Empty, interior-gapped, and older windows fail.
    """
    if not candles or candles[-1].starts_at != expected_last_start - bar_duration:
        return False
    if any(
        second.starts_at - first.starts_at != bar_duration for first, second in pairwise(candles)
    ):
        return False
    closed_at = expected_last_start + bar_duration
    instant = datetime.now(UTC) if now is None else now
    return closed_at <= instant < closed_at + timedelta(seconds=NEWEST_BAR_SETTLE_SECONDS)
