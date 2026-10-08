"""Reference instruments in the unified backtest model (ADR 0096).

The kernel fixture (SMA(2) > 12 on the traded 1h bars) gains a BTC-USD 1d gate:
``btc_close > btc_sma``. The gate decides whether the same price path trades; the
reference never changes fill semantics, and the in-progress daily bar is never read.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from tests.backtest.test_kernel import _candles, _hour, _run, _strategy, _with
from tests.strategies.reference_support import REFERENCE_DATASET, daily_bars
from thytrader.backtest.kernel import BacktestSimulationError, simulate_backtest
from thytrader.backtest.models import backtest_result_fingerprint
from thytrader.evaluation.models import ReferenceInstrumentDataset, ResearchRunSpecification
from thytrader.evaluation.signal_evaluator import evaluate_signal_trace
from thytrader.evaluation.trace import EntryConditionOutcome
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint

if TYPE_CHECKING:
    from thytrader.market_data.models import Candle

_FIRST_DAY = datetime(2026, 7, 30, tzinfo=UTC)


def _gated_strategy() -> StrategyDefinition:
    """The kernel fixture AND BTC-USD's last closed daily close above its 2-day SMA."""
    base = _strategy()
    payload = base.model_dump(mode="python")
    payload["data_requirements"]["reference_instruments"] = (
        {"id": "btc", "product_id": "BTC-USD", "timeframe": "1d"},
    )
    payload["indicators"] = (
        *payload["indicators"],
        {
            "id": "btc_close",
            "kind": "identity",
            "input": "close",
            "parameters": {},
            "source": "btc",
        },
        {
            "id": "btc_sma",
            "kind": "sma",
            "input": "close",
            "parameters": {"period": 2},
            "source": "btc",
        },
    )
    entry_rule = payload["entry"]["when"]["all"][0]
    payload["entry"]["when"] = {
        "all": (
            entry_rule,
            {
                "left": {"indicator": "btc_close"},
                "operator": "greater_than",
                "right": {"indicator": "btc_sma"},
            },
        )
    }
    return _with(
        base, **{key: payload[key] for key in ("data_requirements", "indicators", "entry")}
    )


def _gated_run(strategy: StrategyDefinition) -> ResearchRunSpecification:
    """The kernel run with the BTC reference bound."""
    run = _run(strategy)
    return ResearchRunSpecification.model_validate(
        {
            **run.model_dump(mode="python"),
            "reference_dataset_fingerprints": (
                ReferenceInstrumentDataset(
                    reference_id="btc",
                    product_id="BTC-USD",
                    timeframe="1d",
                    dataset_fingerprint=REFERENCE_DATASET,
                ),
            ),
        }
    )


def _btc(last_closed: str, in_progress: str = "1") -> dict[str, tuple[Candle, ...]]:
    """BTC dailies 07-30 (100) and 07-31 (``last_closed``), plus in-progress 08-01."""
    return {"btc": daily_bars(_FIRST_DAY, "100", last_closed, in_progress)}


def test_bullish_reference_lets_the_fixture_trade() -> None:
    """BTC 07-31 close 200 > SMA 150, so the 02:00 signal rests and fills at 03:00."""
    strategy = _gated_strategy()
    result = simulate_backtest(
        _gated_run(strategy), strategy, _candles(), reference_candles=_btc("200")
    )
    assert len(result.trades) == 1
    assert result.trades[0].entry.candle_starts_at == _hour(3)


def test_bearish_reference_blocks_every_entry() -> None:
    """BTC 07-31 close 50 < SMA 75: the same price path never enters."""
    strategy = _gated_strategy()
    result = simulate_backtest(
        _gated_run(strategy), strategy, _candles(), reference_candles=_btc("50")
    )
    assert result.trades == ()


def test_reference_backtests_are_deterministic_and_ignore_the_in_progress_bar() -> None:
    """Identical inputs give identical results; rewriting the in-progress 08-01 bar does not."""
    strategy = _gated_strategy()
    run = _gated_run(strategy)
    first = simulate_backtest(run, strategy, _candles(), reference_candles=_btc("200", "1"))
    second = simulate_backtest(run, strategy, _candles(), reference_candles=_btc("200", "1"))
    poisoned = simulate_backtest(run, strategy, _candles(), reference_candles=_btc("200", "9999"))
    assert backtest_result_fingerprint(first) == backtest_result_fingerprint(second)
    assert backtest_result_fingerprint(first) == backtest_result_fingerprint(poisoned)


def test_trace_records_reference_values_on_each_decision_bar() -> None:
    """The authoritative trace carries the BTC values every decision bar read."""
    strategy = _gated_strategy()
    trace = evaluate_signal_trace(
        _gated_run(strategy), strategy, _candles(), reference_candles=_btc("200")
    )
    first = trace.records[0]
    values = {item.indicator_id: item.value for item in first.indicator_values}
    assert values["btc_close"] == "200"
    assert values["btc_sma"] == "150"
    assert first.entry_condition is EntryConditionOutcome.MATCHED


def test_reference_strategy_without_reference_candles_is_rejected() -> None:
    """A reference document cannot be simulated without its reference bars."""
    strategy = _gated_strategy()
    with pytest.raises(BacktestSimulationError, match="signal inputs"):
        simulate_backtest(_gated_run(strategy), strategy, _candles())


def test_reference_free_kernel_results_are_unchanged() -> None:
    """The reference plumbing leaves an existing document's result identity untouched."""
    strategy = _strategy()
    assert strategy_fingerprint(strategy) == _run(strategy).strategy_fingerprint
    baseline = simulate_backtest(_run(strategy), strategy, _candles())
    with_empty = simulate_backtest(_run(strategy), strategy, _candles(), reference_candles={})
    assert backtest_result_fingerprint(baseline) == backtest_result_fingerprint(with_empty)
