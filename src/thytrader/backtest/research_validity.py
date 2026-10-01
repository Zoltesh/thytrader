"""Typed research validity limit codes disclosed on backtest summaries."""

from __future__ import annotations

from typing import Literal

from thytrader.strategies.models import StrategyDefinition  # noqa: TC001

ResearchValidityLimitCode = Literal[
    "maker_touch_full_fill",
    "stop_before_tp_same_bar",
    "spot_short_synthetic",
]


def collect_backtest_validity_limits(
    strategy: StrategyDefinition,
) -> tuple[ResearchValidityLimitCode, ...]:
    """Return the modeling limits that apply to one unified backtest result.

    Every result discloses maker touch-full-fill optimism and that a bar touching both
    the stop and the resting take-profit is resolved as the stop (conservative).
    Short strategies also disclose synthetic spot-short inventory.
    """
    limits: list[ResearchValidityLimitCode] = [
        "maker_touch_full_fill",
        "stop_before_tp_same_bar",
    ]
    if strategy.entry.side == "short":
        limits.append("spot_short_synthetic")
    return tuple(limits)
