"""Signal-based exits in the unified backtest model (ADR 0093).

The fixture strategy enters when SMA(2) > 12 and rests its limit at the signal close
(14). Bar 03:00 fills it; the exit rules below are evaluated on every closed bar but may
only act after the fill bar, as a taker at that bar's close, after the stop and the
resting take-profit had their chance.
"""

from __future__ import annotations

from decimal import Decimal
import json
from typing import TYPE_CHECKING

from tests.backtest.test_kernel import _WARMUP, _bars, _hour, _run, _strategy, _with
from thytrader.backtest.kernel import simulate_backtest, simulate_backtest_with_diagnostics
from thytrader.backtest.models import (
    BacktestDiagnostics,
    BacktestExitCount,
    BacktestResult,
    backtest_result_fingerprint,
)
from thytrader.research.signal_evaluator import evaluate_signal_trace
from thytrader.research.trace import (
    EntryConditionOutcome,
    canonical_signal_trace_bytes,
)
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint

if TYPE_CHECKING:
    from thytrader.market_data.models import Candle

_SIGNAL_BAR = ("14", "15", "12", "14")
_FILL_BAR = ("14", "15", "12", "12")
_EXIT_BAR = ("12", "13", "11", "12")
_TERMINAL = ("12", "13", "11", "12")


def _exit_rule(operator: str, literal: str) -> dict[str, object]:
    """``exits.signal_exit`` comparing SMA(2) against one literal."""
    return {
        "when": {
            "all": [
                {
                    "left": {"indicator": "sma"},
                    "operator": operator,
                    "right": {"literal": literal},
                }
            ]
        }
    }


def _strategy_with_exit(
    signal_exit: dict[str, object] | None,
    *,
    take_profit: dict[str, str] | None = None,
    max_bars_held: int = 96,
) -> StrategyDefinition:
    """The kernel fixture with no take-profit (unless given) and an optional exit rule."""
    exits: dict[str, object] = {
        "initial_stop": {"kind": "atr_multiple", "atr_indicator": "atr", "multiple": "2"},
        "take_profit": take_profit or {"kind": "none"},
        "trailing_stop": {"enabled": False},
        "time_exit": {"max_bars_held": max_bars_held},
    }
    if signal_exit is not None:
        exits["signal_exit"] = signal_exit
    return _with(_strategy(), exits=exits)


def _candles(*evaluation: tuple[str, str, str, str]) -> tuple[Candle, ...]:
    """Warmup, the evaluation bars, and the terminal boundary bar."""
    return _bars(*_WARMUP, *evaluation, _TERMINAL)


def _simulate(
    strategy: StrategyDefinition, *evaluation: tuple[str, str, str, str]
) -> tuple[BacktestResult, BacktestDiagnostics]:
    """Run the fixture over 02:00..(02:00 + len(evaluation)) with the terminal bar."""
    return simulate_backtest_with_diagnostics(
        _run(strategy, evaluation_hours=len(evaluation)), strategy, _candles(*evaluation)
    )


def test_matched_exit_rule_sells_at_the_close_as_a_taker_after_the_fill_bar() -> None:
    """SMA < 13.5 already holds on the fill bar (13) but acts on the next close (12)."""
    strategy = _strategy_with_exit(_exit_rule("less_than", "13.5"))
    result, diagnostics = _simulate(strategy, _SIGNAL_BAR, _FILL_BAR, _EXIT_BAR)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry.candle_starts_at == _hour(3)
    assert trade.entry.price == "14"
    assert trade.exit.reason == "signal"
    assert trade.exit.candle_starts_at == _hour(4)
    assert Decimal(trade.exit.price) == Decimal("12") * (Decimal(1) - Decimal("0.001"))
    assert trade.exit.fee_rate == "0.002"
    assert trade.holding_bars == 1
    assert diagnostics.exit_reasons == (BacktestExitCount(reason="signal", count=1),)
    assert "signal_exit_at_close" in (result.summary.validity_limits or ())


def test_exit_rule_never_acts_on_the_fill_bar() -> None:
    """A rule that matches only on the fill bar (SMA = 13) leaves the book to the window end."""
    strategy = _strategy_with_exit(_exit_rule("equals", "13"))
    result, diagnostics = _simulate(strategy, _SIGNAL_BAR, _FILL_BAR, _EXIT_BAR)
    assert [trade.exit.reason for trade in result.trades] == ["evaluation_end"]
    assert result.trades[0].exit.candle_starts_at == _hour(5)
    assert diagnostics.exit_reasons == (BacktestExitCount(reason="evaluation_end", count=1),)


def test_protective_stop_wins_a_same_bar_tie_with_the_exit_rule() -> None:
    """A bar whose low trades through the stop exits as a stop even though the rule matched."""
    strategy = _strategy_with_exit(_exit_rule("less_than", "13.5"))
    result, diagnostics = _simulate(strategy, _SIGNAL_BAR, _FILL_BAR, ("12", "13", "5", "12"))
    assert [trade.exit.reason for trade in result.trades] == ["stop_loss"]
    assert result.trades[0].exit.candle_starts_at == _hour(4)
    assert diagnostics.exit_reasons == (BacktestExitCount(reason="stop_loss", count=1),)


def test_take_profit_touched_inside_the_bar_wins_over_the_close_time_rule() -> None:
    """The resting target fills intrabar, before the close that evaluates the exit rule."""
    strategy = _strategy_with_exit(
        _exit_rule("less_than", "13.5"), take_profit={"kind": "reward_risk", "multiple": "0.5"}
    )
    result, _diagnostics = _simulate(strategy, _SIGNAL_BAR, _FILL_BAR, ("12", "40", "11", "12"))
    assert [trade.exit.reason for trade in result.trades] == ["take_profit"]


def test_exit_rule_precedes_a_time_exit_due_on_the_same_close() -> None:
    """Both sell at the close; the declared rule names the exit."""
    timed = _strategy_with_exit(None, max_bars_held=1)
    timed_result, _ = _simulate(timed, _SIGNAL_BAR, _FILL_BAR, _EXIT_BAR)
    assert [trade.exit.reason for trade in timed_result.trades] == ["time_exit"]
    both = _strategy_with_exit(_exit_rule("less_than", "13.5"), max_bars_held=1)
    both_result, _ = _simulate(both, _SIGNAL_BAR, _FILL_BAR, _EXIT_BAR)
    assert [trade.exit.reason for trade in both_result.trades] == ["signal"]
    assert both_result.trades[0].exit.price == timed_result.trades[0].exit.price


def test_unmatched_exit_rule_leaves_every_other_exit_unchanged() -> None:
    """A rule that never matches produces the same trades as no rule at all."""
    never = _strategy_with_exit(_exit_rule("less_than", "1"))
    plain = _strategy_with_exit(None)
    never_result, _ = _simulate(never, _SIGNAL_BAR, _FILL_BAR, _EXIT_BAR)
    plain_result, _ = _simulate(plain, _SIGNAL_BAR, _FILL_BAR, _EXIT_BAR)
    assert never_result.trades == plain_result.trades
    assert "signal_exit_at_close" not in (plain_result.summary.validity_limits or ())


def test_signal_exit_is_deterministic_and_pays_the_spread_stress() -> None:
    """Identical inputs give one fingerprint; a stressed run sells at the bid."""
    strategy = _strategy_with_exit(_exit_rule("less_than", "13.5"))
    candles = _candles(_SIGNAL_BAR, _FILL_BAR, _EXIT_BAR)
    run = _run(strategy, evaluation_hours=3)
    first = simulate_backtest(run, strategy, candles)
    second = simulate_backtest(run, strategy, candles)
    assert backtest_result_fingerprint(first) == backtest_result_fingerprint(second)
    stressed = simulate_backtest(
        _run(strategy, evaluation_hours=3, spread_bps="20"), strategy, candles
    )
    exit_fill = stressed.trades[0].exit
    assert exit_fill.reason == "signal"
    assert exit_fill.executable_side == "bid"
    assert Decimal(exit_fill.price) < Decimal(first.trades[0].exit.price)


def test_trace_records_the_exit_rule_only_for_strategies_that_declare_one() -> None:
    """Older traces keep their bytes; a declared rule adds ``exit_condition`` per candle."""
    plain = _strategy_with_exit(None)
    plain_trace = evaluate_signal_trace(
        _run(plain, evaluation_hours=3), plain, _candles(_SIGNAL_BAR, _FILL_BAR, _EXIT_BAR)
    )
    assert all(record.exit_condition is None for record in plain_trace.records)
    assert b"exit_condition" not in canonical_signal_trace_bytes(plain_trace)
    declared = _strategy_with_exit(_exit_rule("less_than", "13.5"))
    trace = evaluate_signal_trace(
        _run(declared, evaluation_hours=3), declared, _candles(_SIGNAL_BAR, _FILL_BAR, _EXIT_BAR)
    )
    assert [record.exit_condition for record in trace.records] == [
        EntryConditionOutcome.MATCHED,
        EntryConditionOutcome.MATCHED,
        EntryConditionOutcome.MATCHED,
    ]
    payload = json.loads(canonical_signal_trace_bytes(trace))
    assert payload["records"][0]["exit_condition"] == "matched"
    assert strategy_fingerprint(declared) != strategy_fingerprint(plain)
