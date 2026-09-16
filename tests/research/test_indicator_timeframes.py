"""Closed-bar per-indicator timeframes overlay LTF entry without lookahead."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from typing import cast
from uuid import UUID

from pydantic import ValidationError
import pytest

from tests.research.test_htf_filter import _htf_candles, _ltf_candles
from thytrader.research.models import (
    BarExecutionAssumptions,
    CapitalAssumptions,
    CostAssumptions,
    EvaluationWindow,
    IndicatorTimeframeDataset,
    ResearchRunSpecification,
    WarmupWindow,
)
from thytrader.research.signal_evaluator import SignalEvaluationError, evaluate_signal_trace
from thytrader.research.trace import EntryConditionOutcome
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint

_LTF_DATASET_FINGERPRINT = "sha256:" + "2" * 64
_EXTRA_DATASET_FINGERPRINT = "sha256:" + "4" * 64


def _extra_tf_strategy() -> StrategyDefinition:
    """Return a 5m decision strategy with a 1h SMA on the LTF indicator list."""
    payload = cast(
        "dict[str, object]",
        json.loads(Path("tests/strategies/golden/reference_strategy_v1.json").read_text()),
    )
    payload["timeframe"] = "5m"
    payload["data_requirements"] = {
        "warmup_bars": 2,
        "required_fields": ["open", "high", "low", "close", "volume"],
    }
    payload["indicators"] = [
        {
            "id": "htf_sma",
            "kind": "sma",
            "input": "close",
            "parameters": {"period": 2},
            "timeframe": "1h",
        },
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
                    "left": {"indicator": "htf_sma"},
                    "operator": "greater_than",
                    "right": {"literal": "20"},
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
    return StrategyDefinition.model_validate(payload)


def _run(strategy: StrategyDefinition) -> ResearchRunSpecification:
    """Return a 5m evaluation covering 10:50 through 12:05 UTC."""
    starts_at = datetime(2026, 7, 10, 10, 50, tzinfo=UTC)
    ends_at = datetime(2026, 7, 10, 12, 5, tzinfo=UTC)
    return ResearchRunSpecification(
        schema_version="1.0",
        run_id=UUID("019faf76-6600-7000-8000-000000000065"),
        created_at=datetime(2026, 7, 29, 20, tzinfo=UTC),
        strategy_fingerprint=strategy_fingerprint(strategy),
        dataset_fingerprint=_LTF_DATASET_FINGERPRINT,
        indicator_dataset_fingerprints=(
            IndicatorTimeframeDataset(
                timeframe="1h",
                dataset_fingerprint=_EXTRA_DATASET_FINGERPRINT,
            ),
        ),
        evaluation=EvaluationWindow(starts_at=starts_at, ends_at=ends_at),
        warmup=WarmupWindow(bars=2, starts_at=starts_at - timedelta(minutes=10)),
        capital=CapitalAssumptions(quote_currency="USD", initial_quote_balance="10000"),
        costs=CostAssumptions(
            maker_fee_rate="0.004",
            taker_fee_rate="0.006",
            fixed_slippage_bps="2.5",
        ),
        bar_execution=BarExecutionAssumptions(
            signal_timing="completed_candle_close",
            fill_timing="next_candle_open",
        ),
        engine_contract_version="thytrader-bar-backtest-v1",
        random_seed=42,
    )


def test_evaluator_holds_last_completed_indicator_timeframe_and_ignores_partial() -> None:
    """Extra-TF SMA uses last-completed 1h bars; an in-progress noon hour is ignored."""
    strategy = _extra_tf_strategy()
    extra = {"1h": _htf_candles()}
    trace = evaluate_signal_trace(_run(strategy), strategy, _ltf_candles(), (), extra)
    by_start = {record.candle_starts_at: record for record in trace.records}
    ten_fifty = datetime(2026, 7, 10, 10, 50, tzinfo=UTC)
    ten_fifty_five = datetime(2026, 7, 10, 10, 55, tzinfo=UTC)
    noon = datetime(2026, 7, 10, 12, tzinfo=UTC)
    assert by_start[ten_fifty].entry_condition is EntryConditionOutcome.NOT_MATCHED
    assert by_start[ten_fifty_five].entry_condition is EntryConditionOutcome.MATCHED
    assert by_start[noon].entry_condition is EntryConditionOutcome.MATCHED


def test_evaluator_fails_closed_when_completed_indicator_bar_is_missing() -> None:
    """Missing last-completed extra-TF coverage is a contract failure, not a pass."""
    strategy = _extra_tf_strategy()
    incomplete = {"1h": _htf_candles(include_partial_noon=False)[:-1]}
    with pytest.raises(SignalEvaluationError, match="incomplete or not contiguous"):
        evaluate_signal_trace(_run(strategy), strategy, _ltf_candles(), (), incomplete)


def test_run_spec_rejects_aliased_indicator_dataset_fingerprint() -> None:
    """Extra-TF dataset identity must not alias the decision dataset."""
    strategy = _extra_tf_strategy()
    payload = _run(strategy).model_dump(mode="python")
    payload["indicator_dataset_fingerprints"] = (
        IndicatorTimeframeDataset(timeframe="1h", dataset_fingerprint=_LTF_DATASET_FINGERPRINT),
    )
    with pytest.raises(ValidationError, match="must differ from dataset_fingerprint"):
        ResearchRunSpecification.model_validate(payload)


def test_evaluator_requires_indicator_dataset_identity() -> None:
    """A missing extra-TF fingerprint fails closed for unbound indicator clocks."""
    strategy = _extra_tf_strategy()
    stray = _run(strategy).model_copy(update={"indicator_dataset_fingerprints": ()})
    with pytest.raises(SignalEvaluationError, match="indicator-timeframe datasets"):
        evaluate_signal_trace(stray, strategy, _ltf_candles(), (), {"1h": _htf_candles()})


def test_evaluator_crossover_fires_when_indicator_timeframe_rolls() -> None:
    """Extra-TF crossovers compare mapped previous vs current LTF bars, not every inner LTF bar."""
    payload = _extra_tf_strategy().model_dump(mode="python")
    indicators = list(payload["indicators"])
    indicators.insert(
        1,
        {"id": "level", "kind": "constant", "parameters": {"value": "20"}},
    )
    payload["indicators"] = indicators
    payload["entry"] = {
        "side": "long",
        "when": {
            "all": [
                {
                    "left": {"indicator": "htf_sma"},
                    "operator": "crosses_above",
                    "right": {"indicator": "level"},
                }
            ]
        },
        "cooldown_bars": 0,
        "max_open_positions": 1,
    }
    strategy = StrategyDefinition.model_validate(payload)
    extra = {"1h": _htf_candles()}
    trace = evaluate_signal_trace(_run(strategy), strategy, _ltf_candles(), (), extra)
    by_start = {record.candle_starts_at: record for record in trace.records}
    ten_fifty = datetime(2026, 7, 10, 10, 50, tzinfo=UTC)
    ten_fifty_five = datetime(2026, 7, 10, 10, 55, tzinfo=UTC)
    eleven = datetime(2026, 7, 10, 11, tzinfo=UTC)
    noon = datetime(2026, 7, 10, 12, tzinfo=UTC)
    assert by_start[ten_fifty].entry_condition is EntryConditionOutcome.NOT_MATCHED
    assert by_start[ten_fifty_five].entry_condition is EntryConditionOutcome.MATCHED
    assert by_start[eleven].entry_condition is EntryConditionOutcome.NOT_MATCHED
    assert by_start[noon].entry_condition is EntryConditionOutcome.NOT_MATCHED


def test_empty_indicator_datasets_omitted_from_canonical_json() -> None:
    """Strategies without extra clocks keep research identity stable."""
    starts_at = datetime(2026, 7, 10, 10, 50, tzinfo=UTC)
    specification = ResearchRunSpecification(
        schema_version="1.0",
        run_id=UUID("019faf76-6600-7000-8000-000000000065"),
        created_at=datetime(2026, 7, 29, 20, tzinfo=UTC),
        strategy_fingerprint="sha256:" + "a" * 64,
        dataset_fingerprint=_LTF_DATASET_FINGERPRINT,
        evaluation=EvaluationWindow(starts_at=starts_at, ends_at=starts_at + timedelta(hours=1)),
        warmup=WarmupWindow(bars=2, starts_at=starts_at - timedelta(minutes=10)),
        capital=CapitalAssumptions(quote_currency="USD", initial_quote_balance="10000"),
        costs=CostAssumptions(
            maker_fee_rate="0.004",
            taker_fee_rate="0.006",
            fixed_slippage_bps="2.5",
        ),
        bar_execution=BarExecutionAssumptions(
            signal_timing="completed_candle_close",
            fill_timing="next_candle_open",
        ),
        engine_contract_version="thytrader-bar-signal-v1",
        random_seed=42,
    )
    payload = specification.model_dump(mode="json", exclude_none=True)
    assert "indicator_dataset_fingerprints" not in payload
