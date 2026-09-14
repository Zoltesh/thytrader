"""Closed-candle HTF alignment, fail-closed coverage, and research fingerprinting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
from typing import cast
from uuid import UUID

from pydantic import ValidationError
import pytest

from thytrader.market_data.datasets import DatasetManifest
from thytrader.market_data.models import Candle
from thytrader.research.models import (
    BarExecutionAssumptions,
    CapitalAssumptions,
    CostAssumptions,
    EvaluationWindow,
    ResearchRunSpecification,
    WarmupWindow,
)
from thytrader.research.multi_timeframe import htf_required_coverage, mapped_htf_start
from thytrader.research.publication import (
    ResearchRunPublicationError,
    verify_research_run_eligibility,
)
from thytrader.research.signal_evaluator import SignalEvaluationError, evaluate_signal_trace
from thytrader.research.trace import EntryConditionOutcome
from thytrader.strategies.models import StrategyDefinition, strategy_fingerprint
from thytrader.strategies.publication import PublishedStrategy

_HTF_DATASET_FINGERPRINT = "sha256:" + "3" * 64
_LTF_DATASET_FINGERPRINT = "sha256:" + "2" * 64


def _htf_strategy() -> StrategyDefinition:
    """Return a 5m decision strategy with a 1h SMA HTF filter."""
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
                    "left": {"literal": "1"},
                    "operator": "greater_than_or_equal",
                    "right": {"literal": "0"},
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
    payload["htf_filter"] = {
        "timeframe": "1h",
        "data_requirements": {
            "warmup_bars": 2,
            "required_fields": ["open", "high", "low", "close", "volume"],
        },
        "indicators": [
            {"id": "htf_sma", "kind": "sma", "input": "close", "parameters": {"period": 2}},
        ],
        "when": {
            "all": [
                {
                    "left": {"indicator": "htf_sma"},
                    "operator": "greater_than",
                    "right": {"literal": "20"},
                }
            ]
        },
    }
    return StrategyDefinition.model_validate(payload)


def _candle(starts_at: datetime, close: str) -> Candle:
    """Build one geometrically valid OHLCV bar."""
    value = Decimal(close)
    return Candle(
        starts_at=starts_at,
        open=value,
        high=value + Decimal("1"),
        low=value - Decimal("0.5") if value > 1 else value,
        close=value,
        volume=Decimal("10"),
    )


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
        htf_dataset_fingerprint=_HTF_DATASET_FINGERPRINT,
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


def _ltf_candles() -> tuple[Candle, ...]:
    """Return warmup and evaluation 5m bars from 10:40 through 12:00."""
    start = datetime(2026, 7, 10, 10, 40, tzinfo=UTC)
    count = 17
    return tuple(_candle(start + timedelta(minutes=5 * index), "3") for index in range(count))


def _htf_candles(*, include_partial_noon: bool = True) -> tuple[Candle, ...]:
    """Return closed 1h bars; optional 12:00 bar is a lookahead poison pill."""
    day = datetime(2026, 7, 10, tzinfo=UTC)
    bars = (
        _candle(day.replace(hour=8), "1"),
        _candle(day.replace(hour=9), "1"),
        _candle(day.replace(hour=10), "50"),
        _candle(day.replace(hour=11), "10"),
    )
    if include_partial_noon:
        bars = (*bars, _candle(day.replace(hour=12), "1"))
    return bars


def test_mapped_htf_start_uses_last_completed_hour() -> None:
    """LTF closes map onto completed HTF bars; in-progress hours are unused."""
    assert mapped_htf_start(datetime(2026, 7, 10, 11, tzinfo=UTC), "1h") == datetime(
        2026, 7, 10, 10, tzinfo=UTC
    )
    assert mapped_htf_start(datetime(2026, 7, 10, 10, 55, tzinfo=UTC), "1h") == datetime(
        2026, 7, 10, 9, tzinfo=UTC
    )
    assert mapped_htf_start(datetime(2026, 7, 10, 12, 5, tzinfo=UTC), "1h") == datetime(
        2026, 7, 10, 11, tzinfo=UTC
    )


def test_evaluator_holds_last_completed_htf_and_ignores_partial_hour() -> None:
    """5m decisions use the last completed 1h bar; the in-progress noon hour never participates."""
    strategy = _htf_strategy()
    trace = evaluate_signal_trace(_run(strategy), strategy, _ltf_candles(), _htf_candles())
    by_start = {record.candle_starts_at: record for record in trace.records}
    ten_fifty = datetime(2026, 7, 10, 10, 50, tzinfo=UTC)
    ten_fifty_five = datetime(2026, 7, 10, 10, 55, tzinfo=UTC)
    noon = datetime(2026, 7, 10, 12, tzinfo=UTC)
    assert by_start[ten_fifty].entry_condition is EntryConditionOutcome.NOT_MATCHED
    assert by_start[ten_fifty_five].entry_condition is EntryConditionOutcome.MATCHED
    assert by_start[noon].entry_condition is EntryConditionOutcome.MATCHED
    htf_ids = [value.indicator_id for value in by_start[noon].indicator_values]
    assert htf_ids[-1] == "htf_sma"


def test_evaluator_fails_closed_when_completed_htf_bar_is_missing() -> None:
    """Missing last-completed HTF coverage is a contract failure, not an implicit pass."""
    strategy = _htf_strategy()
    incomplete = _htf_candles(include_partial_noon=False)[:-1]
    with pytest.raises(SignalEvaluationError, match="HTF candle coverage"):
        evaluate_signal_trace(_run(strategy), strategy, _ltf_candles(), incomplete)


def test_evaluator_rejects_htf_candles_without_filter() -> None:
    """A single-timeframe strategy must not silently consume extra HTF candles."""
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
                    "left": {"literal": "1"},
                    "operator": "greater_than_or_equal",
                    "right": {"literal": "0"},
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
    strategy = StrategyDefinition.model_validate(payload)
    starts_at = datetime(2026, 7, 10, 2, tzinfo=UTC)
    specification = ResearchRunSpecification(
        schema_version="1.0",
        run_id=UUID("019faf76-6600-7000-8000-000000000065"),
        created_at=datetime(2026, 7, 29, 20, tzinfo=UTC),
        strategy_fingerprint=strategy_fingerprint(strategy),
        dataset_fingerprint=_LTF_DATASET_FINGERPRINT,
        evaluation=EvaluationWindow(starts_at=starts_at, ends_at=starts_at + timedelta(hours=2)),
        warmup=WarmupWindow(bars=2, starts_at=starts_at - timedelta(hours=2)),
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
    candles = tuple(
        _candle(datetime(2026, 7, 10, tzinfo=UTC) + timedelta(hours=index), "2")
        for index in range(5)
    )
    with pytest.raises(SignalEvaluationError, match="without an HTF filter"):
        evaluate_signal_trace(specification, strategy, candles, _htf_candles())


def test_run_spec_rejects_aliased_htf_dataset_fingerprint() -> None:
    """HTF identity cannot alias the decision dataset."""
    strategy = _htf_strategy()
    with pytest.raises(ValidationError, match="must differ"):
        ResearchRunSpecification(
            schema_version="1.0",
            run_id=UUID("019faf76-6600-7000-8000-000000000065"),
            created_at=datetime(2026, 7, 29, 20, tzinfo=UTC),
            strategy_fingerprint=strategy_fingerprint(strategy),
            dataset_fingerprint=_LTF_DATASET_FINGERPRINT,
            htf_dataset_fingerprint=_LTF_DATASET_FINGERPRINT,
            evaluation=EvaluationWindow(
                starts_at=datetime(2026, 7, 10, 10, 50, tzinfo=UTC),
                ends_at=datetime(2026, 7, 10, 12, 5, tzinfo=UTC),
            ),
            warmup=WarmupWindow(
                bars=2,
                starts_at=datetime(2026, 7, 10, 10, 40, tzinfo=UTC),
            ),
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


def _published(strategy: StrategyDefinition) -> PublishedStrategy:
    """Wrap one validated definition as a publication."""
    return PublishedStrategy(
        strategy_fingerprint=strategy_fingerprint(strategy),
        definition=strategy,
    )


def _manifest(*, timeframe: str, fingerprint: str, starts_at: str, ends_at: str) -> DatasetManifest:
    """Return verified-manifest facts for one product timeframe."""
    return DatasetManifest(
        provider="coinbase",
        product_id="BTC-USD",
        timeframe=timeframe,
        starts_at=starts_at,
        ends_at=ends_at,
        expected_candle_count=4,
        received_candle_count=4,
        gap_count=0,
        missing_intervals=0,
        complete=True,
        content_fingerprint=fingerprint,
        files=(Path("unused.parquet"),),
        manifest_path=Path("unused.json"),
    )


def test_eligibility_requires_htf_dataset_coverage_without_next_open() -> None:
    """HTF coverage is last-completed bars plus warmup; it does not need a next-open fill candle."""
    strategy = _htf_strategy()
    published = _published(strategy)
    specification = _run(strategy)
    assert strategy.htf_filter is not None
    required_start, required_end = htf_required_coverage(
        evaluation_starts_at=specification.evaluation.starts_at,
        evaluation_ends_at=specification.evaluation.ends_at,
        htf_filter=strategy.htf_filter,
    )
    assert required_start == datetime(2026, 7, 10, 8, tzinfo=UTC)
    assert required_end == datetime(2026, 7, 10, 12, tzinfo=UTC)
    ltf_manifest = _manifest(
        timeframe="5m",
        fingerprint=_LTF_DATASET_FINGERPRINT,
        starts_at="2026-07-10T10:40:00Z",
        ends_at="2026-07-10T12:10:00Z",
    )
    htf_manifest = _manifest(
        timeframe="1h",
        fingerprint=_HTF_DATASET_FINGERPRINT,
        starts_at="2026-07-10T08:00:00Z",
        ends_at="2026-07-10T12:00:00Z",
    )
    verify_research_run_eligibility(specification, published, ltf_manifest, htf_manifest)

    with pytest.raises(ResearchRunPublicationError, match="HTF dataset"):
        verify_research_run_eligibility(specification, published, ltf_manifest, None)

    short_htf = _manifest(
        timeframe="1h",
        fingerprint=_HTF_DATASET_FINGERPRINT,
        starts_at="2026-07-10T09:00:00Z",
        ends_at="2026-07-10T12:00:00Z",
    )
    with pytest.raises(ResearchRunPublicationError, match="HTF dataset does not provide"):
        verify_research_run_eligibility(specification, published, ltf_manifest, short_htf)

    stray = specification.model_copy(update={"htf_dataset_fingerprint": None})
    with pytest.raises(ResearchRunPublicationError, match="HTF dataset fingerprint is required"):
        verify_research_run_eligibility(stray, published, ltf_manifest, htf_manifest)
