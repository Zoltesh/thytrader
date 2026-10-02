"""Typed research validity limit codes disclosed on backtest summaries."""

from __future__ import annotations

from typing import Literal

from thytrader.strategies.models import StrategyDefinition, signal_exit_condition

ResearchValidityLimitCode = Literal[
    "maker_touch_full_fill",
    "stop_before_tp_same_bar",
    "spot_short_synthetic",
    "signal_exit_at_close",
]


def collect_backtest_validity_limits(
    strategy: StrategyDefinition,
) -> tuple[ResearchValidityLimitCode, ...]:
    """Return the modeling limits that apply to one unified backtest result.

    Every result discloses maker touch-full-fill optimism and that a bar touching both
    the stop and the resting take-profit is resolved as the stop (conservative).
    Short strategies also disclose synthetic spot-short inventory. Strategies with an
    ``exits.signal_exit`` rule disclose that a matched exit fills as a taker at the
    signal bar's own close, a price paper and live can only approach by selling right
    after that close (ADR 0093); documents without one keep their exact limits.
    """
    limits: list[ResearchValidityLimitCode] = [
        "maker_touch_full_fill",
        "stop_before_tp_same_bar",
    ]
    if strategy.entry.side == "short":
        limits.append("spot_short_synthetic")
    if signal_exit_condition(strategy.exits) is not None:
        limits.append("signal_exit_at_close")
    return tuple(limits)
