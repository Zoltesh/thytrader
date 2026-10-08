"""Operand lags preserve causality, native clocks, and shared runtime semantics."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from pydantic import ValidationError
import pytest

from tests.evaluation.test_htf_filter import (
    _htf_candles,
    _htf_strategy,
    _ltf_candles,
    _run as _htf_run,
)
from tests.evaluation.test_indicator_timeframes import _extra_tf_strategy, _run as _extra_run
from tests.evaluation.test_reference_alignment import (
    _EVALUATION_START,
    _HOURS,
    _decision_candles,
    _references,
)
from tests.evaluation.test_signal_evaluator import _candles, _run, _strategy
from tests.strategies.reference_support import reference_run, reference_strategy
from thytrader.evaluation.indicators import calculate_indicator_rows
from thytrader.evaluation.models import ResearchRunSpecification
from thytrader.evaluation.signal_evaluator import evaluate_signal_trace
from thytrader.evaluation.trace import EntryConditionOutcome
from thytrader.execution.decision_rules import entry_rule_trace, signal_record
from thytrader.execution.decisions import ConditionComparisonTrace, ConditionGroupTrace
from thytrader.execution.signals import (
    evaluate_latest_entry_evidence,
    evaluate_latest_signal_exit,
)
from thytrader.research.parameter_sweep import apply_parameter_cell
from thytrader.strategies.authoring import create_template_strategy
from thytrader.strategies.models import (
    AllCondition,
    ComparisonCondition,
    IndicatorOperand,
    StrategyDefinition,
    canonical_strategy_bytes,
    expanded_data_requirements,
    reference_data_requirements,
    strategy_fingerprint,
    strategy_indicator_operands,
)


def _lagged_strategy(offset: int) -> StrategyDefinition:
    """Validate a small SMA rule that reads a previous native bar."""
    payload = _strategy().model_dump(mode="json", by_alias=True)
    payload["entry"]["when"]["all"][0]["left"]["offset"] = offset
    payload["data_requirements"]["warmup_bars"] = 2 + offset
    return StrategyDefinition.model_validate(payload)


@pytest.mark.parametrize("offset", [-1, 501, 0.5, True, "1"])
def test_operand_offset_rejects_invalid_runtime_values(offset: object) -> None:
    """The document boundary accepts bounded integer lags only."""
    with pytest.raises(ValidationError):
        IndicatorOperand.model_validate({"indicator": "sma", "offset": offset})


def test_zero_offset_preserves_canonical_strategy_bytes() -> None:
    """Explicit zero and omitted offsets identify exactly the same rules."""
    assert canonical_strategy_bytes(_lagged_strategy(0)) == canonical_strategy_bytes(_strategy())
    assert strategy_fingerprint(_lagged_strategy(1)) != strategy_fingerprint(_strategy())


def test_signal_exit_offset_alone_requires_warmup() -> None:
    """Exit reads require history even when the entry uses only today's values."""
    payload = _strategy().model_dump(mode="json", by_alias=True)
    payload["exits"]["signal_exit"] = {
        "when": _lagged_strategy(1).model_dump(mode="json", by_alias=True)["entry"]["when"]
    }
    with pytest.raises(ValidationError, match="warmup_bars"):
        StrategyDefinition.model_validate(payload)


def test_constant_and_literal_operands_reject_positive_offsets() -> None:
    """Literals have no history and constants cannot gain a meaningful lag."""
    payload = _strategy().model_dump(mode="json", by_alias=True)
    payload["entry"]["when"]["all"][0]["right"]["offset"] = 1
    with pytest.raises(ValidationError):
        StrategyDefinition.model_validate(payload)
    payload = _strategy().model_dump(mode="json", by_alias=True)
    payload["indicators"].append({"id": "level", "kind": "constant", "parameters": {"value": "2"}})
    payload["entry"]["when"]["all"][0]["right"] = {"indicator": "level", "offset": 1}
    with pytest.raises(ValidationError, match="constant"):
        StrategyDefinition.model_validate(payload)


def test_operand_offset_reads_hand_calculated_sma_history_without_lookahead() -> None:
    """A two-bar lag reads 1.5 and 3 while preserving today's original SMA output."""
    operand = IndicatorOperand(indicator="sma", offset=2)
    candles = _candles()
    rows = calculate_indicator_rows(_strategy().indicators, candles, operands=(operand,))
    assert [row["sma@2"] for row in rows] == [None, None, None, Decimal("1.5"), Decimal("3")]
    assert rows[3]["sma"] == Decimal("2.5")
    assert (
        calculate_indicator_rows(_strategy().indicators, candles[:4], operands=(operand,))
        == rows[:4]
    )


def test_declaration_and_operand_offsets_add_on_the_same_clock() -> None:
    """An existing declaration lag is preserved and the operand adds its own lag."""
    strategy = _lagged_strategy(1)
    payload = strategy.model_dump(mode="json", by_alias=True)
    payload["indicators"][0]["offset"] = 1
    with pytest.raises(ValidationError, match="warmup_bars"):
        StrategyDefinition.model_validate(payload)
    payload["data_requirements"]["warmup_bars"] = 4
    strategy = StrategyDefinition.model_validate(payload)
    rows = calculate_indicator_rows(
        strategy.indicators, _candles(), operands=strategy_indicator_operands(strategy)
    )
    assert rows[3]["sma@1"] == Decimal("1.5")
    assert rows[3]["sma"] == Decimal("3")


def test_missing_lagged_history_remains_undefined_under_not() -> None:
    """Negation cannot turn a missing old value into an entry."""
    payload = _lagged_strategy(3).model_dump(mode="json", by_alias=True)
    payload["entry"]["when"] = {"not": payload["entry"]["when"]}
    strategy = StrategyDefinition.model_validate(payload)
    evidence = evaluate_latest_entry_evidence(strategy, _candles()[:2])
    assert evidence.outcome is EntryConditionOutcome.UNDEFINED


def test_research_runtime_and_journal_use_identical_lagged_entry_and_exit_reads() -> None:
    """The journal records the historical values actually used by both runtime rules."""
    payload = _lagged_strategy(1).model_dump(mode="json", by_alias=True)
    payload["exits"]["signal_exit"] = {"when": payload["entry"]["when"]}
    strategy = StrategyDefinition.model_validate(payload)
    run_payload = _run(strategy).model_dump(mode="python")
    run_payload["warmup"]["bars"] = 3
    run_payload["evaluation"]["starts_at"] += timedelta(hours=1)
    run_payload["evaluation"]["ends_at"] += timedelta(hours=1)
    run = ResearchRunSpecification.model_validate(run_payload)
    trace = evaluate_signal_trace(run, strategy, _candles())
    assert [row.entry_condition for row in trace.records] == ["matched", "not_matched"]
    for index, row in enumerate(trace.records, 4):
        evidence = evaluate_latest_entry_evidence(strategy, _candles()[:index])
        exit_evidence = evaluate_latest_signal_exit(strategy, _candles()[:index])
        assert evidence.outcome is row.entry_condition
        assert exit_evidence is not None
        assert exit_evidence.outcome is row.exit_condition
        recorded = signal_record(strategy, evidence)
        assert recorded is not None
        assert recorded.indicator_values == row.indicator_values
        explained = entry_rule_trace(strategy, evidence)
        assert explained is not None
        assert isinstance(explained.entry, ConditionGroupTrace)
        leaf = explained.entry.children[0]
        assert isinstance(leaf, ConditionComparisonTrace)
        assert leaf.left.key == "sma@1"
        assert leaf.left.label == "SMA(2) (1 bar ago)"
        values = {value.indicator_id: value.value for value in row.indicator_values}
        assert leaf.left.value == values["sma@1"]


def test_crossovers_compare_each_operands_current_and_previous_lag() -> None:
    """A lagged SMA crosses its two-bar-old value using t-1/t-2 and t-2/t-3."""
    payload = _lagged_strategy(2).model_dump(mode="json", by_alias=True)
    payload["entry"]["when"]["all"][0] = {
        "left": {"indicator": "sma", "offset": 1},
        "operator": "crosses_below",
        "right": {"indicator": "sma", "offset": 2},
    }
    strategy = StrategyDefinition.model_validate(payload)
    evidence = evaluate_latest_entry_evidence(strategy, _candles())
    assert evidence.outcome is EntryConditionOutcome.MATCHED
    assert evidence.current["sma@1"] == Decimal("2.5")
    assert evidence.previous is not None
    assert evidence.previous["sma@2"] == Decimal("1.5")


@pytest.mark.parametrize("filter_mode", [False, True], ids=["extra-clock", "htf-filter"])
def test_native_clock_offset_is_applied_before_alignment(filter_mode: bool) -> None:
    """Offset 1 on a 1h SMA means one hour, even when decisions run every 5 minutes."""
    base = _htf_strategy() if filter_mode else _extra_tf_strategy()
    payload = base.model_dump(mode="json", by_alias=True)
    when = payload["htf_filter"]["when"] if filter_mode else payload["entry"]["when"]
    when["all"][0]["left"]["offset"] = 1
    if filter_mode:
        payload["htf_filter"]["data_requirements"]["warmup_bars"] = 3
    strategy = StrategyDefinition.model_validate(payload)
    if not filter_mode:
        assert expanded_data_requirements(strategy)[1].warmup_bars == 3
    run = _htf_run(strategy) if filter_mode else _extra_run(strategy)
    bars = _htf_candles()
    bars = (
        replace(
            bars[0],
            starts_at=bars[0].starts_at - timedelta(hours=1),
            open=Decimal("10"),
            high=Decimal("11"),
            low=Decimal("9.5"),
            close=Decimal("10"),
        ),
        *bars,
    )
    trace = evaluate_signal_trace(
        run,
        strategy,
        _ltf_candles(),
        bars if filter_mode else (),
        None if filter_mode else {"1h": bars},
    )
    for row in trace.records:
        prefix = tuple(c for c in _ltf_candles() if c.starts_at <= row.candle_starts_at)
        evidence = evaluate_latest_entry_evidence(
            strategy,
            prefix,
            bars if filter_mode else (),
            None if filter_mode else {"1h": bars},
        )
        assert evidence.outcome is row.entry_condition
    first_values = {value.indicator_id: value.value for value in trace.records[0].indicator_values}
    assert first_values["htf_sma@1"] == "5.5"
    assert first_values["htf_sma"] == "1"


def test_squeeze_sweep_updates_both_current_and_prior_band_reads() -> None:
    """One period axis changes both uses of the shared Bollinger definition."""
    base = create_template_strategy(template="squeeze-breakout")
    candidate = apply_parameter_cell(
        base, (("bands", "period", "30"),), base_fingerprint=strategy_fingerprint(base)
    )
    assert {indicator.id for indicator in candidate.indicators} == {
        "close",
        "bands",
        "channel",
        "atr",
    }
    assert candidate.data_requirements.warmup_bars == 31
    assert isinstance(candidate.entry.when, AllCondition)
    prior = candidate.entry.when.all[0]
    current = candidate.entry.when.all[2]
    assert isinstance(prior, ComparisonCondition)
    assert isinstance(current, ComparisonCondition)
    assert prior.left == IndicatorOperand(indicator="bands", series="upper", offset=1)
    assert current.right == IndicatorOperand(indicator="bands", series="upper")


def test_reference_operand_lags_daily_history_before_hourly_alignment() -> None:
    """A reference lag follows completed daily bars and agrees in research and runtime."""
    payload = reference_strategy().model_dump(mode="json", by_alias=True)
    payload["entry"]["when"]["all"][0]["left"]["offset"] = 2
    strategy = StrategyDefinition.model_validate(payload)
    assert reference_data_requirements(strategy)[0].warmup_bars == 3
    run = reference_run(strategy, starts_at=_EVALUATION_START, hours=_HOURS)
    trace = evaluate_signal_trace(
        run, strategy, _decision_candles(), reference_candles=_references()
    )
    poisoned = evaluate_signal_trace(
        run, strategy, _decision_candles(), reference_candles=_references("100000")
    )
    assert trace == poisoned
    for row in trace.records:
        prefix = tuple(c for c in _decision_candles() if c.starts_at <= row.candle_starts_at)
        evidence = evaluate_latest_entry_evidence(strategy, prefix, reference_candles=_references())
        assert evidence.outcome is row.entry_condition
        assert evidence.current["btc_close@2"] == Decimal("100")
        values = {value.indicator_id: value.value for value in row.indicator_values}
        assert values["btc_close@2"] == "100"
