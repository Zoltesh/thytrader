"""Fail-closed in-sample ranking of parameter-sweep and WFO candidates."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from thytrader.backtest.models import BacktestSummary


class SelectionMetric(StrEnum):
    """Fail-closed in-sample ranking metrics for sweeps and WFO."""

    TOTAL_RETURN_FRACTION = "total_return_fraction"
    TOTAL_NET_PNL = "total_net_pnl"
    MAXIMUM_DRAWDOWN_FRACTION = "maximum_drawdown_fraction"


def select_candidate_fingerprint(
    scored: tuple[tuple[str, str], ...],
    metric: SelectionMetric,
) -> str:
    """Pick one fingerprint from in-sample scores. Ties use lexicographic order."""
    if not scored:
        raise ValueError("selection requires at least one in-sample score")
    minimize = metric is SelectionMetric.MAXIMUM_DRAWDOWN_FRACTION

    def _key(item: tuple[str, str]) -> tuple[Decimal, str]:
        fingerprint, text = item
        value = Decimal(text)
        ordered = value if minimize else -value
        return (ordered, fingerprint)

    return min(scored, key=_key)[0]


def metric_value(summary: BacktestSummary, metric: SelectionMetric) -> str:
    """Read the canonical decimal string for one selection metric."""
    if metric is SelectionMetric.TOTAL_RETURN_FRACTION:
        return summary.total_return_fraction
    if metric is SelectionMetric.TOTAL_NET_PNL:
        return summary.total_net_pnl
    return summary.maximum_drawdown_fraction
