"""Behavior tests for deterministic bar-level backtest simulation."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
import json
from pathlib import Path
from typing import cast
from uuid import UUID

from pydantic import ValidationError
import pytest

from thytrader.backtest.kernel import BacktestSimulationError, simulate_backtest
from thytrader.backtest.models import BacktestResult, canonical_backtest_result_bytes
from thytrader.market_data.models import Candle
from thytrader.research.models import (
    BarExecutionAssumptions,
    BrokerAssumptions,
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


def _v2_run(strategy: StrategyDefinition, spread_bps: str) -> ResearchRunSpecification:
    """Build one V2 run with fully disclosed constant-spread execution assumptions."""
    v1 = _run(strategy)
    return ResearchRunSpecification.model_validate(
        {
            **v1.model_dump(mode="python"),
            "broker": BrokerAssumptions(
                price_model="constant_spread_bps",
                spread_bps=spread_bps,
                fill_policy="full",
                trigger_evaluation="bid_side",
                equity_marking="bid_close",
            ),
            "engine_contract_version": "thytrader-bar-backtest-v2",
        }
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


def test_simulation_rejects_naive_terminal_candle_with_controlled_error() -> None:
    """A malformed next-open timestamp must not escape as an aware/naive TypeError."""
    strategy = _strategy()
    candles = (
        *_candles()[:-1],
        replace(_candles()[-1], starts_at=_candles()[-1].starts_at.replace(tzinfo=None)),
    )

    with pytest.raises(BacktestSimulationError, match="candles"):
        simulate_backtest(_run(strategy), strategy, candles)


def test_v2_zero_spread_preserves_v1_economics_and_records_executable_evidence() -> None:
    """Zero spread preserves V1 economics while V2 records its distinct disclosed contract."""
    strategy = _strategy()
    v1 = simulate_backtest(_run(strategy), strategy, _candles())
    v2 = simulate_backtest(_v2_run(strategy, "0"), strategy, _candles())

    assert v2.engine_contract_version == "thytrader-bar-backtest-v2"
    assert v2.broker is not None
    assert v2.broker.spread_bps == "0"
    assert v2.summary.total_spread_cost == "0"
    assert tuple(trade.net_pnl for trade in v2.trades) == tuple(
        trade.net_pnl for trade in v1.trades
    )
    assert v2.summary.final_equity == v1.summary.final_equity
    assert v2.trades[0].entry.executable_side == "ask"
    assert v2.trades[0].exit.executable_side == "bid"
    assert v2.trades[0].entry.spread_cost == "0"
    assert v2.trades[0].exit.spread_cost == "0"


def test_v2_spread_is_monotonic_and_identity_bearing() -> None:
    """Higher disclosed spread cannot improve a long-only simulated result."""
    strategy = _strategy()
    low = simulate_backtest(_v2_run(strategy, "10"), strategy, _candles())
    high = simulate_backtest(_v2_run(strategy, "25"), strategy, _candles())

    assert Decimal(high.summary.final_equity) < Decimal(low.summary.final_equity)
    assert Decimal(high.summary.total_spread_cost or "0") > Decimal(
        low.summary.total_spread_cost or "0"
    )
    assert high.run_fingerprint != low.run_fingerprint


def test_simulation_exits_a_gap_through_stop_at_the_adverse_open() -> None:
    """A stop crossed before intrabar trading must use the worse executable opening price."""
    strategy = _strategy()
    gap_candles = (
        *_candles()[:3],
        Candle(
            starts_at=datetime(2026, 8, 1, 3, tzinfo=UTC),
            open=Decimal("15"),
            high=Decimal("16"),
            low=Decimal("14"),
            close=Decimal("15"),
            volume=Decimal("10"),
        ),
        Candle(
            starts_at=datetime(2026, 8, 1, 4, tzinfo=UTC),
            open=Decimal("8"),
            high=Decimal("10"),
            low=Decimal("7"),
            close=Decimal("9"),
            volume=Decimal("10"),
        ),
        Candle(
            starts_at=datetime(2026, 8, 1, 5, tzinfo=UTC),
            open=Decimal("9"),
            high=Decimal("10"),
            low=Decimal("8"),
            close=Decimal("9"),
            volume=Decimal("10"),
        ),
    )
    run = _run(strategy).model_copy(
        update={
            "evaluation": EvaluationWindow(
                starts_at=datetime(2026, 8, 1, 2, tzinfo=UTC),
                ends_at=datetime(2026, 8, 1, 5, tzinfo=UTC),
            )
        }
    )

    result = simulate_backtest(run, strategy, gap_candles)

    assert result.trades[0].exit.reason == "stop_loss"
    assert result.trades[0].exit.price == "7.992"


def test_simulation_prefers_stop_when_stop_and_target_are_reached_in_one_bar() -> None:
    """Ambiguous same-bar protective triggers use the conservative V1 stop-first policy."""
    strategy = _strategy()
    collision = (
        *_candles()[:3],
        Candle(
            starts_at=datetime(2026, 8, 1, 3, tzinfo=UTC),
            open=Decimal("15"),
            high=Decimal("30"),
            low=Decimal("9"),
            close=Decimal("10"),
            volume=Decimal("10"),
        ),
        _candles()[-1],
    )

    result = simulate_backtest(_run(strategy), strategy, collision)

    assert result.trades[0].exit.reason == "stop_loss"


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
    assert result.summary.final_equity == "10000"
    assert result.summary.average_win is None
    assert result.summary.average_loss is None
    assert result.summary.profit_factor is None
    assert result.summary.maximum_drawdown == "0"
    canonical = canonical_backtest_result_bytes(result)

    assert BacktestResult.model_validate_json(canonical) == result


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
