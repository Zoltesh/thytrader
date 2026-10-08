"""Behavior vectors for optional economic gates and deterministic execution stresses."""

from decimal import Context, Decimal, localcontext

from pydantic import ValidationError
import pytest

from tests.backtest.test_kernel import _bars, _candles, _run, _strategy
from thytrader.backtest.kernel import simulate_backtest, simulate_backtest_with_diagnostics
from thytrader.evaluation.models import research_run_fingerprint
from thytrader.evaluation.stress import ExecutionStress
from thytrader.execution.economics import (
    EconomicEntryGuard,
    EconomicPreflightRequest,
    economic_preflight,
)
from thytrader.strategies.models import StrategyDefinition, canonical_strategy_bytes
from thytrader.trading.geometry import EntrySkipReason


@pytest.mark.parametrize(
    ("side", "target", "stop", "net"),
    [("long", "100.5", "98", "-0.5025"), ("short", "99.5", "102", "-0.4975")],
)
def test_positive_gross_target_can_lose_after_both_fees(
    side: str, target: str, stop: str, net: str
) -> None:
    """A half-percent target loses at the observed half-percent maker tier in both directions."""
    result = economic_preflight(
        EconomicPreflightRequest.model_validate(
            {
                "side": side,
                "entry_price": "100",
                "target_price": target,
                "stop_price": stop,
                "maker_fee_rate": "0.005",
                "taker_fee_rate": "0.009",
            }
        )
    )
    assert result.net_target_quote_pnl is not None
    assert Decimal(result.net_target_quote_pnl) == Decimal(net)
    assert result.target_clears_costs is False
    assert Decimal(result.net_stop_quote_pnl) < -2


def test_omitted_features_preserve_canonical_strategy_and_run() -> None:
    """Defaults neither add fields nor silently refingerprint existing evidence."""
    strategy = _strategy()
    original = canonical_strategy_bytes(strategy)
    payload = strategy.model_dump(mode="python")
    payload["entry"]["economic_guard"] = None
    assert canonical_strategy_bytes(StrategyDefinition.model_validate(payload)) == original
    run = _run(strategy)
    assert research_run_fingerprint(run) == research_run_fingerprint(
        run.model_copy(update={"costs": run.costs.model_copy(update={"execution_stress": None})})
    )


def test_guard_counts_rejected_signals_without_orders() -> None:
    """A deliberately unreachable hurdle refuses entries through the shared skip vocabulary."""
    strategy = _strategy()
    strategy = strategy.model_copy(
        update={
            "entry": strategy.entry.model_copy(
                update={
                    "economic_guard": EconomicEntryGuard(minimum_net_target_return_fraction="1")
                }
            )
        }
    )
    result, diagnostics = simulate_backtest_with_diagnostics(_run(strategy), strategy, _candles())
    assert result.summary.trade_count == 0
    assert diagnostics.entries_rested == 0
    assert any(
        skip.reason is EntrySkipReason.NET_TARGET_BELOW_MINIMUM for skip in diagnostics.skipped
    )


def test_latency_can_miss_a_window_and_partial_fill_changes_quantity() -> None:
    """Stress changes actual simulated fills, is deterministic, and changes run identity."""
    strategy, candles = _strategy(), _candles()
    run = _run(strategy)
    base = simulate_backtest(run, strategy, candles)
    delayed = run.model_copy(
        update={
            "costs": run.costs.model_copy(
                update={"execution_stress": ExecutionStress(entry_latency_bars=1)}
            )
        }
    )
    assert simulate_backtest(delayed, strategy, candles).summary.trade_count == 0
    partial = run.model_copy(
        update={
            "costs": run.costs.model_copy(
                update={"execution_stress": ExecutionStress(entry_fill_fraction="0.5")}
            )
        }
    )
    stressed = simulate_backtest(partial, strategy, candles)
    assert stressed == simulate_backtest(partial, strategy, candles)
    with localcontext(Context(prec=64)):
        assert (
            Decimal(stressed.trades[0].entry.quantity) == Decimal(base.trades[0].entry.quantity) / 2
        )
    assert research_run_fingerprint(partial) != research_run_fingerprint(run)


def test_penetration_refuses_touch_only_fill() -> None:
    """A candle that only touches the posted entry cannot fill a penetrated maker profile."""
    strategy = _strategy()
    candles = _bars(
        ("10", "11", "9", "10"),
        ("11", "12", "10", "11"),
        ("14", "15", "12", "14"),
        ("15", "16", "14", "15"),
        ("15", "16", "14", "15"),
    )
    run = _run(strategy)
    assert simulate_backtest(run, strategy, candles).summary.trade_count == 1
    stressed = run.model_copy(
        update={
            "costs": run.costs.model_copy(
                update={"execution_stress": ExecutionStress(maker_penetration_bps="1")}
            )
        }
    )
    assert simulate_backtest(stressed, strategy, candles).summary.trade_count == 0


def test_rejects_impossible_stress() -> None:
    """Invalid fractions cannot create excess inventory or an infinite latency."""
    with pytest.raises(ValidationError):
        ExecutionStress(entry_fill_fraction="1.1")
    with pytest.raises(ValidationError):
        ExecutionStress(entry_latency_bars=101)
