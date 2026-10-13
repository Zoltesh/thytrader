"""Consumer-time quality and immutable source identity for every as-seen auxiliary clock."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from thytrader.market_data.models import parse_candle_interval
from thytrader.market_data.quality import validate_candle_timestamp, validate_candle_values
from thytrader.strategies.models import extra_indicator_timeframes, reference_instruments

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from thytrader.market_data.models import Candle
    from thytrader.replay.steps import CandleWindow, ReplayStep
    from thytrader.strategies.models import StrategyDefinition

EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def validate_window(rows: Sequence[Candle], timeframe: str, available: datetime) -> None:
    """Validate UTC/value semantics before comparisons; no future, gaps or duplicate bars."""
    duration = parse_candle_interval(timeframe).duration
    previous = None
    for candle in rows:
        validate_candle_timestamp(candle.starts_at)
        validate_candle_values(candle)
        try:
            if (candle.starts_at - EPOCH) % duration:
                raise ValueError("auxiliary candle is not aligned to its clock")
            if candle.starts_at + duration > available:
                raise ValueError("future auxiliary candle in as-seen window")
            if previous is not None and candle.starts_at != previous + duration:
                raise ValueError("auxiliary window is not contiguous")
        except OverflowError as error:
            raise ValueError("auxiliary boundary is not representable") from error
        previous = candle.starts_at


def validate_named_windows(
    windows: Sequence[CandleWindow], identities: Mapping[str, tuple[str, str]], available: datetime
) -> None:
    """Refuse unknown, repeated or misidentified named series before dictionary conversion."""
    seen: set[str] = set()
    for window in windows:
        if window.key in seen or identities.get(window.key) != (
            window.product_id,
            window.timeframe,
        ):
            raise ValueError("auxiliary source identity is unknown, duplicated or inconsistent")
        seen.add(window.key)
        validate_window(window.candles, window.timeframe, available)


def validate_auxiliary_windows(step: ReplayStep, strategy: StrategyDefinition) -> None:
    """Apply the same as-seen trust boundary to HTF, indicator clocks and reference products."""
    if step.htf_candles:
        if strategy.htf_filter is None:
            raise ValueError("HTF bars have no declared source clock")
        validate_window(step.htf_candles, strategy.htf_filter.timeframe, step.data_available_at)
    indicator_identities = {
        clock: (step.product.product_id, clock) for clock in extra_indicator_timeframes(strategy)
    }
    reference_identities = {
        item.id: (item.product_id, item.timeframe) for item in reference_instruments(strategy)
    }
    validate_named_windows(step.indicator_windows, indicator_identities, step.data_available_at)
    validate_named_windows(step.reference_windows, reference_identities, step.data_available_at)
