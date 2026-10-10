"""Typed research validity limit codes disclosed on backtest summaries."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from thytrader.evaluation.stress import ExecutionStress

from thytrader.strategies.models import StrategyDefinition, signal_exit_condition

ResearchValidityLimitCode = Literal[
    "maker_touch_full_fill",
    "stop_before_tp_same_bar",
    "spot_short_synthetic",
    "signal_exit_at_close",
    "synthetic_no_trade_bars",
    "execution_stress",
    "futures_constant_margin",
    "futures_conservative_liquidation",
    "futures_shared_usdc_collateral",
    "futures_constant_funding",
    "futures_funding_at_bar_close",
]


def collect_backtest_validity_limits(
    strategy: StrategyDefinition,
    *,
    no_trade_bars: int = 0,
    execution_stress: ExecutionStress | None = None,
    futures_limits: tuple[ResearchValidityLimitCode, ...] = (),
) -> tuple[ResearchValidityLimitCode, ...]:
    """Return the modeling limits that apply to one unified backtest result.

    Every result discloses maker touch-full-fill optimism and that a bar touching both
    the stop and the resting take-profit is resolved as the stop (conservative).
    Short strategies also disclose synthetic spot-short inventory. Strategies with an
    ``exits.signal_exit`` rule disclose that a matched exit fills as a taker at the
    signal bar's own close, a price paper and live can only approach by selling right
    after that close (ADR 0093); documents without one keep their exact limits.
    ``no_trade_bars`` counts flat zero-volume bars in the evaluation window (confirmed
    intervals without trades, ADR 0095); any such bar discloses
    ``synthetic_no_trade_bars``, and gap-free windows keep their exact limits.
    Futures shorts are real shorts, so they never disclose ``spot_short_synthetic``;
    ``futures_limits`` (ADR 0128) are appended last.
    """
    limits: list[ResearchValidityLimitCode] = [
        "stop_before_tp_same_bar",
    ]
    if execution_stress is None:
        limits.insert(0, "maker_touch_full_fill")
    else:
        limits.insert(0, "execution_stress")
    if strategy.entry.side == "short" and not strategy.instrument.is_future:
        limits.append("spot_short_synthetic")
    if signal_exit_condition(strategy.exits) is not None:
        limits.append("signal_exit_at_close")
    if no_trade_bars > 0:
        limits.append("synthetic_no_trade_bars")
    limits.extend(futures_limits)
    return tuple(limits)
