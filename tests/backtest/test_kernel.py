"""Behavior tests for deterministic bar-level backtest simulation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
import json
from pathlib import Path
from typing import cast
from uuid import UUID

from pydantic import ValidationError
import pytest

from thytrader.backtest.kernel import BacktestSimulationError, simulate_backtest
from thytrader.backtest.models import canonical_backtest_result_bytes
from thytrader.market_data.models import Candle
from thytrader.research.models import (
    BarExecutionAssumptions,
    CapitalAssumptions,
    CostAssumptions,
    EvaluationWindow,
    ResearchRunSpecification,
    WarmupWindow,
)
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint


def _strategy() -> StrategyDefinition:
    """Build the narrow published profile used by the first simulation vector."""
    payload = cast(
        "dict[str, object]",
        json.loads(Path("tests/strategies/golden/reference_strategy_v1.json").read_text()),
    )
    payload["data_requirements"] = {
        "warmup_bars": 2,
        "required_fields": ["open", "high", "low", "close", "volume"],
    }
    payload["indicators"] = [
        {"id": "sma", "kind": "sma", "input": "close", "parameters": {"period": 2}},
        {
            "id": "atr",
            "kind": "atr",
            "input": ["high", "low", "close"],
            "parameters": {"period": 2},
        },
    ]
    payload["entry"] = {
        "side": "long",
        "when": {
            "all": [
                {
                    "left": {"indicator": "sma"},
                    "operator": "greater_than",
                    "right": {"literal": "12"},
                }
            ]
        },
        "cooldown_bars": 0,
        "max_open_positions": 1,
    }
    payload["exits"] = {
        "initial_stop": {"kind": "atr_multiple", "atr_indicator": "atr", "multiple": "2"},
        "take_profit": {"kind": "reward_risk", "multiple": "2"},
        "trailing_stop": {"enabled": False},
        "time_exit": {"max_bars_held": 96},
    }
    payload["sizing"] = {
        "kind": "risk_fraction",
        "risk_fraction": "0.01",
        "min_quote_notional": "1",
        "max_quote_notional": "1000",
    }
    payload["portfolio_limits"] = {
        "max_strategy_exposure_fraction": "1",
        "max_concurrent_positions": 1,
    }
    return StrategyDefinition.model_validate(payload)


def _run(strategy: StrategyDefinition) -> ResearchRunSpecification:
    """Build one executable run with two evaluation bars and one required fill bar."""
    starts_at = datetime(2026, 8, 1, 2, tzinfo=UTC)
    return ResearchRunSpecification(
        schema_version="1.0",
        run_id=UUID("019cae99-3e00-7000-8000-000000000001"),
        created_at=datetime(2026, 3, 2, 12, 50, 4, 416000, tzinfo=UTC),
        strategy_fingerprint=strategy_fingerprint(strategy),
        dataset_fingerprint="sha256:" + "a" * 64,
        evaluation=EvaluationWindow(starts_at=starts_at, ends_at=starts_at + timedelta(hours=2)),
        warmup=WarmupWindow(bars=2, starts_at=starts_at - timedelta(hours=2)),
        capital=CapitalAssumptions(quote_currency="USD", initial_quote_balance="10000"),
        costs=CostAssumptions(
            maker_fee_rate="0.001",
            taker_fee_rate="0.002",
            fixed_slippage_bps="10",
        ),
        bar_execution=BarExecutionAssumptions(
            signal_timing="completed_candle_close",
            fill_timing="next_candle_open",
        ),
        engine_contract_version="thytrader-bar-backtest-v1",
        random_seed=0,
    )


def _candles() -> tuple[Candle, ...]:
    """Return warmup, one signal, one filled target, and one required final fill candle."""
    start = datetime(2026, 8, 1, tzinfo=UTC)
    rows = (
        ("10", "11", "9", "10"),
        ("11", "12", "10", "11"),
        ("14", "15", "12", "14"),
        ("15", "30", "10", "10"),
        ("10", "11", "9", "10"),
    )
    return tuple(
        Candle(
            starts_at=start + timedelta(hours=index),
            open=Decimal(open_),
            high=Decimal(high),
            low=Decimal(low),
            close=Decimal(close),
            volume=Decimal("10"),
        )
        for index, (open_, high, low, close) in enumerate(rows)
    )


def test_simulation_fills_at_next_open_applies_taker_costs_and_closes_at_target() -> None:
    """A close-time signal must not fill until next open and target fills use conservative costs."""
    strategy = _strategy()
    result = simulate_backtest(_run(strategy), strategy, _candles())

    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry.candle_starts_at == datetime(2026, 8, 1, 3, tzinfo=UTC)
    assert trade.entry.price == "15.015"
    assert trade.entry.fee_rate == "0.002"
    assert trade.exit.reason == "take_profit"
    assert trade.exit.price == "26.987985"
    assert Decimal(trade.net_pnl) > Decimal("0")
    assert result.summary.trade_count == 1
    assert result.summary.final_equity == result.equity_curve[-1].equity
    assert result.summary.win_rate == "1"
    assert result.summary.profit_factor is None
    total_return = Decimal(result.summary.total_return_fraction)
    total_net_pnl = Decimal(result.summary.total_net_pnl)
    assert total_return == total_net_pnl / Decimal("10000")


def test_simulation_rejects_a_signal_only_research_run() -> None:
    """A signal-only immutable run must never silently gain fill and PnL semantics."""
    strategy = _strategy()
    signal_only_run = _run(strategy).model_copy(
        update={"engine_contract_version": "thytrader-bar-signal-v1"}
    )

    with pytest.raises(BacktestSimulationError, match="backtest engine contract"):
        simulate_backtest(signal_only_run, strategy, _candles())


def test_simulation_skips_a_zero_atr_entry_without_failing() -> None:
    """A valid flat market produces no ATR-sized entry instead of division by zero."""
    strategy = _strategy()
    flat_candles = tuple(
        Candle(
            starts_at=datetime(2026, 8, 1, hour, tzinfo=UTC),
            open=Decimal("14"),
            high=Decimal("14"),
            low=Decimal("14"),
            close=Decimal("14"),
            volume=Decimal("10"),
        )
        for hour in range(5)
    )

    result = simulate_backtest(_run(strategy), strategy, flat_candles)

    assert result.trades == ()
    assert result.summary.trade_count == 0


@pytest.mark.parametrize("noncanonical", ["10000.0", "-0"])
def test_result_fingerprint_rejects_noncanonical_decimal_rendering(noncanonical: str) -> None:
    """Equivalent Decimal spellings must never create different immutable result identities."""
    strategy = _strategy()
    result = simulate_backtest(_run(strategy), strategy, _candles())
    forged = result.model_copy(
        update={"summary": result.summary.model_copy(update={"initial_equity": noncanonical})}
    )

    with pytest.raises(ValidationError, match="canonical plain decimal"):
        canonical_backtest_result_bytes(forged)


def test_final_fill_candle_non_open_values_do_not_affect_simulation() -> None:
    """Future OHLC values beyond the final next-open fill boundary must be ignored."""
    strategy = _strategy()
    baseline = simulate_backtest(_run(strategy), strategy, _candles())
    final_fill = _candles()[-1]
    future_mutated = (
        *_candles()[:-1],
        Candle(
            starts_at=final_fill.starts_at,
            open=final_fill.open,
            high=Decimal("100"),
            low=Decimal("1"),
            close=Decimal("50"),
            volume=Decimal("999999"),
        ),
    )

    assert simulate_backtest(_run(strategy), strategy, future_mutated) == baseline


def test_simulation_decimal_results_ignore_ambient_decimal_precision() -> None:
    """Simulation identity must remain unchanged when unrelated process Decimal settings change."""
    strategy = _strategy()
    baseline = simulate_backtest(_run(strategy), strategy, _candles())

    with localcontext() as context:
        context.prec = 6
        changed_context = simulate_backtest(_run(strategy), strategy, _candles())

    assert changed_context == baseline
