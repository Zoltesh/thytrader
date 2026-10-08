"""Behavior tests for deterministic bar-level backtest simulation."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Context, Decimal, localcontext
import json
from pathlib import Path
from typing import cast
from uuid import UUID

from pydantic import ValidationError
import pytest

from thytrader.backtest.kernel import BacktestSimulationError, simulate_backtest
from thytrader.backtest.models import (
    BacktestResult,
    backtest_result_fingerprint,
    canonical_backtest_result_bytes,
)
from thytrader.evaluation.models import (
    CapitalAssumptions,
    CostAssumptions,
    EvaluationWindow,
    ResearchRunSpecification,
    WarmupWindow,
)
from thytrader.evaluation.signal_evaluator import evaluate_signal_trace
from thytrader.evaluation.trace import combined_signal_trace_fingerprint, signal_trace_fingerprint
from thytrader.market_data.models import Candle
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


def _run(
    strategy: StrategyDefinition,
    *,
    evaluation_hours: int = 2,
    slippage_bps: str = "10",
    spread_bps: str | None = None,
) -> ResearchRunSpecification:
    """Build one executable run with evaluation bars from 02:00 plus the terminal bar."""
    starts_at = datetime(2026, 8, 1, 2, tzinfo=UTC)
    costs = CostAssumptions(
        maker_fee_rate="0.001",
        taker_fee_rate="0.002",
        fixed_slippage_bps=slippage_bps,
    )
    if spread_bps is not None:
        costs = costs.model_copy(update={"spread_bps": spread_bps})
    return ResearchRunSpecification(
        schema_version="1.0",
        run_id=UUID("019cae99-3e00-7000-8000-000000000001"),
        created_at=datetime(2026, 3, 2, 12, 50, 4, 416000, tzinfo=UTC),
        strategy_fingerprint=strategy_fingerprint(strategy),
        dataset_fingerprint="sha256:" + "a" * 64,
        evaluation=EvaluationWindow(
            starts_at=starts_at, ends_at=starts_at + timedelta(hours=evaluation_hours)
        ),
        warmup=WarmupWindow(bars=2, starts_at=starts_at - timedelta(hours=2)),
        capital=CapitalAssumptions(quote_currency="USD", initial_quote_balance="10000"),
        costs=costs,
        random_seed=0,
    )


def _bars(*rows: tuple[str, str, str, str]) -> tuple[Candle, ...]:
    """Build hourly OHLC candles from 00:00 UTC (two warmup bars precede evaluation)."""
    start = datetime(2026, 8, 1, tzinfo=UTC)
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


_WARMUP = (("10", "11", "9", "10"), ("11", "12", "10", "11"))
_SIGNAL = ("14", "15", "12", "14")


def _candles() -> tuple[Candle, ...]:
    """Return warmup, one signal bar, one fill bar, and the terminal boundary bar."""
    return _bars(*_WARMUP, _SIGNAL, ("15", "30", "10", "10"), ("10", "11", "9", "10"))


def _hour(value: int) -> datetime:
    """Return one hourly UTC boundary on the fixture day."""
    return datetime(2026, 8, 1, value, tzinfo=UTC)


def _short_strategy() -> StrategyDefinition:
    """Reuse the kernel fixture with published short entry geometry."""
    payload = _strategy().model_dump(mode="python")
    payload["entry"]["side"] = "short"
    return StrategyDefinition.model_validate(payload)


def _with(strategy: StrategyDefinition, **overrides: object) -> StrategyDefinition:
    """Return a revalidated strategy with top-level section overrides."""
    return StrategyDefinition.model_validate({**strategy.model_dump(mode="python"), **overrides})


def test_matched_signal_rests_a_close_limit_and_fills_when_a_later_low_trades_through() -> None:
    """The signal bar never fills; the next bar fills at the posted limit as a maker."""
    strategy = _strategy()
    result = simulate_backtest(_run(strategy), strategy, _candles())

    assert result.engine == "thytrader-backtest"
    assert len(result.trades) == 1
    entry = result.trades[0].entry
    assert entry.candle_starts_at == _hour(3)
    assert entry.price == "14"
    assert entry.fee_rate == "0.001"
    with localcontext(Context(prec=64)):
        assert Decimal(entry.fee) == Decimal(entry.notional) * Decimal("0.001")
    assert entry.executable_side is None
    assert entry.reference_price is None


def test_take_profit_is_not_eligible_on_the_fill_bar() -> None:
    """The take-profit rests only after the fill bar, so a fill-bar spike cannot take profit."""
    strategy = _strategy()
    result = simulate_backtest(_run(strategy), strategy, _candles())

    trade = result.trades[0]
    assert trade.exit.reason == "evaluation_end"
    assert trade.exit.candle_starts_at == _hour(4)
    assert trade.exit.price == "9.99"
    assert trade.exit.fee_rate == "0.002"
    assert Decimal(trade.net_pnl) < 0


def test_unfilled_entry_cancels_after_max_entry_wait_bars() -> None:
    """Bars that never trade through the limit expire the resting buy instead of filling it."""
    strategy = _strategy()
    candles = _bars(
        *_WARMUP, ("14", "15", "13", "14"), ("16", "17", "15", "16"), ("18", "19", "17", "18")
    )
    result = simulate_backtest(_run(strategy), strategy, candles)

    assert result.trades == ()
    assert result.summary.final_equity == "10000"


def test_unfilled_entry_reprices_at_the_expiry_bar_close() -> None:
    """After max wait, a reprice rests at the current close and can fill on a later bar."""
    strategy = _with(
        _strategy(),
        execution={
            "entry_preference": "maker_only",
            "max_entry_wait_bars": 2,
            "on_unfilled_entry": "reprice",
        },
    )
    candles = _bars(
        *_WARMUP,
        ("14", "15", "13", "14"),
        ("16", "17", "15", "16"),
        ("18", "19", "17", "18"),
        ("18", "19", "17.5", "18"),
        ("18", "19", "17.5", "18"),
    )
    result = simulate_backtest(_run(strategy, evaluation_hours=4), strategy, candles)

    assert result.trades[0].entry.candle_starts_at == _hour(5)
    assert result.trades[0].entry.price == "18"
    assert result.trades[0].entry.fee_rate == "0.001"


def test_stop_is_checked_first_on_the_fill_bar_even_when_the_target_is_also_touched() -> None:
    """Same-bar stop and target on the fill bar resolve stop-first: the target is not resting."""
    strategy = _strategy()
    candles = _bars(*_WARMUP, _SIGNAL, ("14", "40", "1", "10"), ("10", "11", "9", "10"))
    result = simulate_backtest(_run(strategy), strategy, candles)

    trade = result.trades[0]
    assert trade.entry.candle_starts_at == _hour(3)
    assert trade.exit.reason == "stop_loss"
    assert trade.exit.candle_starts_at == _hour(3)
    assert trade.exit.fee_rate == "0.002"
    assert trade.exit.price == "7.992"


def test_stop_wins_when_a_later_bar_touches_both_stop_and_take_profit() -> None:
    """A bar touching both exits cannot show which traded first, so the stop is assumed."""
    strategy = _strategy()
    candles = _bars(
        *_WARMUP,
        _SIGNAL,
        ("14", "15", "13.5", "14"),
        ("14", "40", "1", "20"),
        ("20", "21", "19", "20"),
    )
    result = simulate_backtest(_run(strategy, evaluation_hours=3), strategy, candles)

    trade = result.trades[0]
    assert trade.exit.reason == "stop_loss"
    assert trade.exit.candle_starts_at == _hour(4)
    assert trade.exit.fee_rate != "0.001"
    assert "stop_before_tp_same_bar" in (result.summary.validity_limits or ())


def test_take_profit_still_fills_when_the_stop_is_not_touched() -> None:
    """A later bar that reaches the target without trading through the stop exits at the target."""
    strategy = _strategy()
    candles = _bars(
        *_WARMUP,
        _SIGNAL,
        ("14", "15", "13.5", "14"),
        ("14", "40", "13", "20"),
        ("20", "21", "19", "20"),
    )
    result = simulate_backtest(_run(strategy, evaluation_hours=3), strategy, candles)

    trade = result.trades[0]
    assert trade.exit.reason == "take_profit"
    assert trade.exit.price == "26"
    assert trade.exit.fee_rate == "0.001"


def test_stop_gapped_through_exits_at_the_adverse_open_with_taker_slippage() -> None:
    """A later bar that opens below the stop exits at that open, not at the stop."""
    strategy = _strategy()
    candles = _bars(
        *_WARMUP,
        _SIGNAL,
        ("14", "15", "13.5", "14"),
        ("6", "7", "5", "6"),
        ("6", "7", "5", "6"),
    )
    result = simulate_backtest(_run(strategy, evaluation_hours=3), strategy, candles)

    assert result.trades[0].exit.reason == "stop_loss"
    assert result.trades[0].exit.price == "5.994"


def test_taker_slippage_applies_to_stop_exits_but_not_maker_entries() -> None:
    """fixed_slippage_bps moves the stop exit while the resting entry stays at its limit."""
    strategy = _strategy()
    candles = _bars(*_WARMUP, _SIGNAL, ("14", "15", "1", "10"), ("10", "11", "9", "10"))
    no_slippage = simulate_backtest(_run(strategy, slippage_bps="0"), strategy, candles)
    with_slippage = simulate_backtest(_run(strategy, slippage_bps="100"), strategy, candles)

    assert no_slippage.trades[0].exit.price == "8"
    assert with_slippage.trades[0].exit.price == "7.92"
    assert no_slippage.trades[0].entry.price == with_slippage.trades[0].entry.price == "14"


def test_time_exit_sells_at_the_close_as_a_taker() -> None:
    """A position held max_bars_held completed bars sells at that bar's close."""
    strategy = _with(
        _strategy(),
        exits={**_strategy().exits.model_dump(mode="python"), "time_exit": {"max_bars_held": 1}},
    )
    candles = _bars(
        *_WARMUP,
        _SIGNAL,
        ("14", "15", "13.5", "14"),
        ("14", "15", "13.5", "15"),
        ("15", "16", "14", "15"),
    )
    result = simulate_backtest(_run(strategy, evaluation_hours=3), strategy, candles)

    trade = result.trades[0]
    assert trade.exit.reason == "time_exit"
    assert trade.exit.candle_starts_at == _hour(4)
    assert trade.exit.price == "14.985"
    assert trade.holding_bars == 1


def test_open_inventory_liquidates_at_the_terminal_open_without_intrabar_processing() -> None:
    """The evaluation-end bar only liquidates at its open; its extremes never trigger exits."""
    strategy = _strategy()
    candles = _bars(
        *_WARMUP,
        _SIGNAL,
        ("14", "15", "13.5", "14"),
        ("14", "15", "13.5", "14"),
        ("20", "40", "1", "25"),
    )
    result = simulate_backtest(
        _run(strategy, evaluation_hours=3, slippage_bps="0"), strategy, candles
    )

    assert result.trades[0].exit.reason == "evaluation_end"
    assert result.trades[0].exit.candle_starts_at == _hour(5)
    assert result.trades[0].exit.price == "20"
    assert result.summary.evaluation_bars == 3


def test_equity_curve_marks_every_evaluation_close_plus_the_terminal_boundary() -> None:
    """One point per evaluation bar close and one flat point at evaluation end."""
    strategy = _strategy()
    result = simulate_backtest(_run(strategy), strategy, _candles())

    assert [point.candle_starts_at for point in result.equity_curve] == [
        _hour(2),
        _hour(3),
        _hour(4),
    ]
    assert Decimal(result.equity_curve[1].base_quantity) > 0
    assert result.equity_curve[1].mark_price == "10"
    assert result.equity_curve[-1].base_quantity == "0"
    assert result.equity_curve[-1].equity == result.summary.final_equity


def test_every_result_discloses_its_validity_limits() -> None:
    """Summaries disclose maker touch-fill optimism and resting-target ordering."""
    strategy = _strategy()
    result = simulate_backtest(_run(strategy), strategy, _candles())

    assert result.summary.validity_limits == ("maker_touch_full_fill", "stop_before_tp_same_bar")


def test_short_rests_a_sell_limit_and_stops_on_the_fill_bar_spike() -> None:
    """Shorts sell to open at the limit when a later high trades through, then cover on stop."""
    strategy = _short_strategy()
    candles = _bars(*_WARMUP, _SIGNAL, ("14", "40", "13", "20"), ("20", "21", "19", "20"))
    result = simulate_backtest(_run(strategy), strategy, candles)

    trade = result.trades[0]
    assert trade.entry.candle_starts_at == _hour(3)
    assert trade.entry.price == "14"
    assert trade.exit.reason == "stop_loss"
    assert trade.exit.price == "20.02"
    assert Decimal(trade.net_pnl) < 0
    assert "spot_short_synthetic" in (result.summary.validity_limits or ())


def test_short_take_profit_rests_below_and_marks_negative_inventory() -> None:
    """A short's resting target fills on a later low; open shorts mark as negative base."""
    strategy = _short_strategy()
    candles = _bars(
        *_WARMUP,
        _SIGNAL,
        ("14", "15", "13.5", "14"),
        ("10", "11", "1", "5"),
        ("5", "6", "4", "5"),
    )
    result = simulate_backtest(_run(strategy, evaluation_hours=3), strategy, candles)

    trade = result.trades[0]
    assert trade.exit.reason == "take_profit"
    assert trade.exit.price == "2"
    assert Decimal(trade.net_pnl) > 0
    assert Decimal(result.equity_curve[1].base_quantity) < 0


def test_five_minute_timeframe_steps_on_five_minute_bars() -> None:
    """A 5m strategy rests, fills, and liquidates on five-minute candles."""
    strategy = _with(_strategy(), timeframe="5m")
    starts_at = datetime(2026, 8, 1, 2, tzinfo=UTC)
    run = _run(strategy).model_copy(
        update={
            "evaluation": EvaluationWindow(
                starts_at=starts_at, ends_at=starts_at + timedelta(minutes=10)
            ),
            "warmup": WarmupWindow(bars=2, starts_at=starts_at - timedelta(minutes=10)),
        }
    )
    start = datetime(2026, 8, 1, 1, 50, tzinfo=UTC)
    candles = tuple(
        replace(candle, starts_at=start + timedelta(minutes=5 * index))
        for index, candle in enumerate(_candles())
    )
    result = simulate_backtest(run, strategy, candles)

    assert result.trades[0].entry.candle_starts_at == datetime(2026, 8, 1, 2, 5, tzinfo=UTC)
    assert result.trades[0].exit.reason == "evaluation_end"
    assert result.summary.evaluation_bars == 2


def test_zero_spread_stress_is_byte_identical_to_the_default() -> None:
    """An explicit spread_bps of 0 is the default model: same run and result bytes."""
    strategy = _strategy()
    default = simulate_backtest(_run(strategy), strategy, _candles())
    explicit = simulate_backtest(_run(strategy, spread_bps="0"), strategy, _candles())

    assert canonical_backtest_result_bytes(explicit) == canonical_backtest_result_bytes(default)
    assert default.summary.total_spread_cost is None
    assert all(trade.exit.executable_side is None for trade in default.trades)


def test_spread_stress_hits_taker_exits_only_and_is_monotonic() -> None:
    """Spread widens taker exits (bid side) while maker entries stay at the posted limit."""
    strategy = _strategy()
    low = simulate_backtest(_run(strategy, spread_bps="10"), strategy, _candles())
    high = simulate_backtest(_run(strategy, spread_bps="25"), strategy, _candles())
    none = simulate_backtest(_run(strategy), strategy, _candles())

    for result in (low, high):
        assert result.trades[0].entry.price == "14"
        assert result.trades[0].entry.executable_side is None
        assert result.trades[0].exit.executable_side == "bid"
        assert result.trades[0].exit.reference_price == "10"
    assert low.trades[0].exit.price == "9.985005"
    assert Decimal(high.summary.final_equity) < Decimal(low.summary.final_equity)
    assert Decimal(low.summary.final_equity) < Decimal(none.summary.final_equity)
    assert Decimal(high.summary.total_spread_cost or "0") > Decimal(
        low.summary.total_spread_cost or "0"
    )
    assert high.run_fingerprint != low.run_fingerprint != none.run_fingerprint


def test_spread_stress_triggers_long_stops_on_the_stressed_bid_and_marks_at_bid() -> None:
    """A low just above the stop triggers it once half the spread is subtracted."""
    strategy = _strategy()
    candles = _bars(*_WARMUP, _SIGNAL, ("14", "15", "8.005", "10"), ("10", "11", "9", "10"))
    unstressed = simulate_backtest(_run(strategy), strategy, candles)
    stressed = simulate_backtest(_run(strategy, spread_bps="20"), strategy, candles)

    assert unstressed.trades[0].exit.reason == "evaluation_end"
    assert unstressed.equity_curve[1].mark_price == "10"
    assert stressed.trades[0].exit.reason == "stop_loss"
    assert stressed.trades[0].exit.candle_starts_at == _hour(3)
    assert stressed.trades[0].exit.price == "7.992"


def test_result_identity_is_deterministic() -> None:
    """Repeated simulations of the same inputs produce identical bytes and fingerprints."""
    strategy = _strategy()
    first = simulate_backtest(_run(strategy), strategy, _candles())
    second = simulate_backtest(_run(strategy), strategy, _candles())

    assert backtest_result_fingerprint(first) == backtest_result_fingerprint(second)
    assert canonical_backtest_result_bytes(first) == canonical_backtest_result_bytes(second)
    assert b'"engine":"thytrader-backtest"' in canonical_backtest_result_bytes(first)


def test_result_identity_changes_with_every_execution_assumption() -> None:
    """Fees, slippage, and spread stress each bind into the result fingerprint."""
    strategy = _strategy()
    baseline = backtest_result_fingerprint(simulate_backtest(_run(strategy), strategy, _candles()))
    variants = {
        backtest_result_fingerprint(simulate_backtest(run, strategy, _candles()))
        for run in (
            _run(strategy, slippage_bps="11"),
            _run(strategy, spread_bps="5"),
            _run(strategy).model_copy(
                update={"costs": _run(strategy).costs.model_copy(update={"maker_fee_rate": "0"})}
            ),
        )
    }

    assert baseline not in variants
    assert len(variants) == 3


def test_simulation_rejects_naive_terminal_candle_with_controlled_error() -> None:
    """A malformed terminal timestamp must not escape as an aware/naive TypeError."""
    strategy = _strategy()
    candles = (
        *_candles()[:-1],
        replace(_candles()[-1], starts_at=_candles()[-1].starts_at.replace(tzinfo=None)),
    )

    with pytest.raises(BacktestSimulationError, match="candles"):
        simulate_backtest(_run(strategy), strategy, candles)


def test_simulation_rejects_unrepresentable_terminal_boundary_with_controlled_error() -> None:
    """A terminal boundary beyond datetime.max must not leak OverflowError."""
    strategy = _strategy()
    run = _run(strategy).model_copy(
        update={
            "evaluation": EvaluationWindow(
                starts_at=datetime(9999, 12, 31, 22, tzinfo=UTC),
                ends_at=datetime(9999, 12, 31, 23, tzinfo=UTC),
            ),
            "warmup": WarmupWindow(bars=2, starts_at=datetime(9999, 12, 31, 20, tzinfo=UTC)),
        }
    )
    candles = tuple(
        Candle(
            starts_at=datetime(9999, 12, 31, hour, tzinfo=UTC),
            open=Decimal("10"),
            high=Decimal("11"),
            low=Decimal("9"),
            close=Decimal("10"),
            volume=Decimal("10"),
        )
        for hour in range(20, 24)
    )

    with pytest.raises(BacktestSimulationError, match=r"candles|coverage"):
        simulate_backtest(run, strategy, candles)


def test_run_specification_rejects_the_removed_engine_selector() -> None:
    """Canonical runs cannot carry the retired engine-contract field."""
    strategy = _strategy()
    payload = {
        **_run(strategy).model_dump(mode="python"),
        "engine_contract_version": "thytrader-backtest",
    }

    with pytest.raises(ValidationError):
        ResearchRunSpecification.model_validate(payload)


def test_simulation_skips_a_zero_atr_entry_without_failing() -> None:
    """A valid flat market produces no ATR-sized entry instead of division by zero."""
    strategy = _strategy()
    flat_candles = tuple(
        Candle(
            starts_at=_hour(hour),
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
    assert result.summary.final_equity == "10000"
    assert result.summary.average_win is None
    assert result.summary.average_loss is None
    assert result.summary.profit_factor is None
    assert result.summary.maximum_drawdown == "0"
    assert BacktestResult.model_validate_json(canonical_backtest_result_bytes(result)) == result


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


def test_terminal_bar_values_other_than_open_do_not_affect_simulation() -> None:
    """Only the evaluation-end bar's open is read; its other OHLCV values are ignored."""
    strategy = _strategy()
    baseline = simulate_backtest(_run(strategy), strategy, _candles())
    terminal = _candles()[-1]
    future_mutated = (
        *_candles()[:-1],
        replace(
            terminal,
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


def _multi_instrument_strategy(max_concurrent_positions: int = 2) -> StrategyDefinition:
    """Reuse the kernel fixture with one extra Coinbase USD spot product."""
    payload = _strategy().model_dump(mode="python")
    payload["additional_instruments"] = [
        {"product_id": "ETH-USD", "base_currency": "ETH", "quote_currency": "USD"}
    ]
    payload["portfolio_limits"]["max_concurrent_positions"] = max_concurrent_positions
    return StrategyDefinition.model_validate(payload)


def _quiet_candles() -> tuple[Candle, ...]:
    """Return aligned bars that never satisfy the kernel SMA greater-than-12 entry."""
    return _bars(*(("10", "11", "9", "10"),) * 5)


def _doubled(candles: tuple[Candle, ...]) -> tuple[Candle, ...]:
    """Scale every price by two so fills identify which product traded."""
    two = Decimal("2")
    return tuple(
        replace(
            candle,
            open=candle.open * two,
            high=candle.high * two,
            low=candle.low * two,
            close=candle.close * two,
        )
        for candle in candles
    )


def test_multi_instrument_backtest_requires_extra_product_candles() -> None:
    """Multi-instrument simulation must fail closed when extra candles are missing."""
    strategy = _multi_instrument_strategy()
    with pytest.raises(BacktestSimulationError, match="additional_instrument_candles"):
        simulate_backtest(_run(strategy), strategy, _candles())


def test_multi_instrument_backtest_keeps_primary_trace_and_evaluates_extras() -> None:
    """Stored signal identity stays the primary product; extras still evaluate fail-closed."""
    strategy = _multi_instrument_strategy()
    run = _run(strategy)
    extras = {"ETH-USD": _quiet_candles()}
    result = simulate_backtest(run, strategy, _candles(), additional_instrument_candles=extras)
    btc_trace = evaluate_signal_trace(run, strategy, _candles())
    eth_trace = evaluate_signal_trace(run, strategy, extras["ETH-USD"])
    assert result.summary.trade_count == 1
    assert result.signal_trace_fingerprint == signal_trace_fingerprint(btc_trace)
    combined = combined_signal_trace_fingerprint({"BTC-USD": btc_trace, "ETH-USD": eth_trace})
    assert combined != result.signal_trace_fingerprint


def test_multi_instrument_books_share_cash_in_product_order_under_the_position_cap() -> None:
    """Products process in product_id order; the cap blocks later books from opening."""
    capped = _multi_instrument_strategy(max_concurrent_positions=1)
    both = _multi_instrument_strategy(max_concurrent_positions=2)
    extras = {"ETH-USD": _doubled(_candles())}

    capped_result = simulate_backtest(
        _run(capped), capped, _candles(), additional_instrument_candles=extras
    )
    both_result = simulate_backtest(
        _run(both), both, _candles(), additional_instrument_candles=extras
    )

    assert [trade.entry.price for trade in capped_result.trades] == ["14"]
    assert sorted(trade.entry.price for trade in both_result.trades) == ["14", "28"]
    with localcontext(Context(prec=64)):
        spent = sum(
            (Decimal(trade.entry.notional) + Decimal(trade.entry.fee))
            for trade in both_result.trades
        )
        drift = abs(Decimal(both_result.equity_curve[1].cash) - (Decimal("10000") - spent))
    assert drift < Decimal("1e-50")


def test_pyramiding_add_rests_as_a_maker_and_averages_into_the_open_position() -> None:
    """A profitable same-side add rests at the signal close and VWAPs into the position."""
    base = _strategy()
    strategy = _with(
        base,
        entry={
            **base.entry.model_dump(mode="python"),
            "max_open_positions": 2,
            "pyramiding": {"enabled": True},
        },
    )
    candles = _bars(
        *_WARMUP,
        _SIGNAL,
        ("14", "15", "13.5", "15"),
        ("15", "16", "14.5", "16"),
        ("16", "17", "15", "16"),
    )
    single = simulate_backtest(_run(base, evaluation_hours=3), base, candles)
    pyramided = simulate_backtest(_run(strategy, evaluation_hours=3), strategy, candles)

    assert len(pyramided.trades) == 1
    entry = pyramided.trades[0].entry
    assert entry.candle_starts_at == _hour(3)
    assert Decimal("14") < Decimal(entry.price) < Decimal("15")
    assert Decimal(entry.quantity) > Decimal(single.trades[0].entry.quantity)
    assert entry.fee_rate == "0.001"
    assert pyramided.trades[0].exit.reason == "evaluation_end"
