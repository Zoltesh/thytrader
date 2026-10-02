"""Backtests disclose evaluated no-trade bars (ADR 0095)."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from tests.backtest.test_kernel import _WARMUP, _bars, _run, _strategy
from thytrader.backtest.kernel import simulate_backtest
from thytrader.research.backtest_model import backtest_model_description

_SIGNAL = ("14", "15", "12", "14")
_QUIET = ("14", "14", "14", "14")
_TERMINAL = ("12", "13", "11", "12")


def test_a_window_with_a_flat_zero_volume_bar_discloses_it() -> None:
    """A no-trade bar inside the evaluation window adds ``synthetic_no_trade_bars``."""
    strategy = _strategy()
    candles = list(_bars(*_WARMUP, _SIGNAL, _QUIET, _TERMINAL))
    candles[3] = replace(candles[3], volume=Decimal(0))

    filled = simulate_backtest(_run(strategy, evaluation_hours=2), strategy, tuple(candles))
    plain = simulate_backtest(
        _run(strategy, evaluation_hours=2), strategy, _bars(*_WARMUP, _SIGNAL, _QUIET, _TERMINAL)
    )

    assert "synthetic_no_trade_bars" in (filled.summary.validity_limits or ())
    assert "synthetic_no_trade_bars" not in (plain.summary.validity_limits or ())


def test_a_flat_bar_outside_the_evaluation_window_is_not_disclosed() -> None:
    """Warmup-only no-trade bars do not change an otherwise identical result."""
    strategy = _strategy()
    candles = list(_bars(*_WARMUP, _SIGNAL, _QUIET, _TERMINAL))
    candles[0] = replace(candles[0], volume=Decimal(0))

    result = simulate_backtest(_run(strategy, evaluation_hours=2), strategy, tuple(candles))

    assert "synthetic_no_trade_bars" not in (result.summary.validity_limits or ())


def test_the_backtest_model_names_no_trade_bars() -> None:
    """Agents reading the model description learn how no-trade bars are simulated."""
    keys = [assumption.key for assumption in backtest_model_description().assumptions]

    assert keys[-1] == "no_trade_bars"
