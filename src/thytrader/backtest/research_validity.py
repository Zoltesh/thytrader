"""Typed research validity limit codes disclosed on backtest summaries."""

from __future__ import annotations

from typing import Literal

from thytrader.research.models import ResearchRunSpecification  # noqa: TC001
from thytrader.strategies.models import StrategyDefinition  # noqa: TC001

ResearchValidityLimitCode = Literal[
    "maker_touch_full_fill",
    "tp_before_stop_same_bar",
    "spot_short_synthetic",
]


def collect_backtest_validity_limits(
    strategy: StrategyDefinition,
    specification: ResearchRunSpecification,
) -> tuple[ResearchValidityLimitCode, ...]:
    """Return F23 limit codes that apply to one V4 backtest result."""
    if specification.engine_contract_version != "thytrader-bar-backtest-v4":
        return ()
    limits: list[ResearchValidityLimitCode] = [
        "maker_touch_full_fill",
        "tp_before_stop_same_bar",
    ]
    if strategy.entry.side == "short":
        limits.append("spot_short_synthetic")
    return tuple(limits)
