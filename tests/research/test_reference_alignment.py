"""Closed-bar alignment of reference instruments in research (ADR 0096).

ETH-USD 1h decisions read BTC-USD 1d bars. At each decision close only the last daily
bar whose close is at or before that instant is visible: the bar closing exactly at
midnight is eligible on the decision bar that also closes at midnight, and the
in-progress daily bar never participates (changing it changes nothing).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from tests.strategies.reference_support import (
    candle,
    daily_bars,
    hourly_bars,
    reference_run,
    reference_strategy,
)
from thytrader.research.models import (
    ReferenceInstrumentDataset,
    ResearchRunSpecification,
    canonical_research_run_bytes,
    research_run_fingerprint,
)
from thytrader.research.signal_evaluator import (
    SignalEvaluationError,
    calculate_reference_indicator_rows,
    evaluate_signal_trace,
)
from thytrader.research.trace import EntryConditionOutcome, SignalTrace

if TYPE_CHECKING:
    from thytrader.market_data.models import Candle

_EVALUATION_START = datetime(2026, 7, 10, 20, tzinfo=UTC)
_HOURS = 8
_FIRST_DAY = datetime(2026, 7, 7, tzinfo=UTC)


def _references(in_progress_close: str = "1") -> dict[str, tuple[Candle, ...]]:
    """BTC dailies 07-07..07-10 closed, plus an in-progress 07-11 lookahead poison bar."""
    return {"btc": daily_bars(_FIRST_DAY, "100", "100", "90", "200", in_progress_close)}


def _decision_candles() -> tuple[Candle, ...]:
    """Two warmup hours plus eight evaluation hours from 18:00 on 07-10."""
    return hourly_bars(_EVALUATION_START - timedelta(hours=2), 2 + _HOURS)


def _trace(in_progress_close: str = "1") -> SignalTrace:
    """Evaluate the fixture over 07-10 20:00 .. 07-11 04:00."""
    strategy = reference_strategy()
    return evaluate_signal_trace(
        reference_run(strategy, starts_at=_EVALUATION_START, hours=_HOURS),
        strategy,
        _decision_candles(),
        reference_candles=_references(in_progress_close),
    )


def _value(trace: SignalTrace, hour: datetime, key: str) -> Decimal | None:
    """Read one traced indicator value on one decision bar."""
    record = next(item for item in trace.records if item.candle_starts_at == hour)
    value = next(item.value for item in record.indicator_values if item.indicator_id == key)
    return None if value is None else Decimal(value)


def test_reference_bar_is_visible_only_after_it_closes() -> None:
    """Before midnight the 07-09 bar gates; the decision bar closing at 00:00 sees 07-10."""
    trace = _trace()
    by_start = {record.candle_starts_at: record for record in trace.records}
    eight_pm = datetime(2026, 7, 10, 20, tzinfo=UTC)
    eleven_pm = datetime(2026, 7, 10, 23, tzinfo=UTC)
    after_midnight = datetime(2026, 7, 11, 2, tzinfo=UTC)
    assert by_start[eight_pm].entry_condition is EntryConditionOutcome.NOT_MATCHED
    assert _value(trace, eight_pm, "btc_close") == Decimal(90)
    assert _value(trace, eight_pm, "btc_sma") == Decimal(95)
    assert by_start[datetime(2026, 7, 10, 22, tzinfo=UTC)].entry_condition is (
        EntryConditionOutcome.NOT_MATCHED
    )
    assert by_start[eleven_pm].entry_condition is EntryConditionOutcome.MATCHED
    assert _value(trace, eleven_pm, "btc_close") == Decimal(200)
    assert by_start[after_midnight].entry_condition is EntryConditionOutcome.MATCHED
    assert _value(trace, after_midnight, "btc_close") == Decimal(200)


def test_in_progress_reference_bar_never_changes_the_trace() -> None:
    """Rewriting the in-progress 07-11 daily bar leaves every record identical (no lookahead)."""
    assert _trace("1") == _trace("100000")


def test_trace_lists_reference_indicator_values_beside_decision_values() -> None:
    """Reference indicator ids are traced like any other declared indicator."""
    trace = _trace()
    assert trace.indicator_ids == ("atr", "btc_close", "btc_sma")


def test_same_timeframe_reference_reads_the_bar_that_closes_with_the_decision_bar() -> None:
    """A 1h reference on a 1h strategy reads the reference bar sharing the decision close."""
    strategy = reference_strategy(reference_timeframe="1h")
    start = datetime(2026, 7, 10, 10, tzinfo=UTC)
    reference = tuple(
        candle(start - timedelta(hours=3) + timedelta(hours=index), str(100 + index * 10))
        for index in range(3 + 4)
    )
    trace = evaluate_signal_trace(
        reference_run(strategy, starts_at=start, hours=4),
        strategy,
        hourly_bars(start - timedelta(hours=2), 2 + 4),
        reference_candles={"btc": reference},
    )
    ten = trace.records[0]
    assert ten.candle_starts_at == start
    assert next(v.value for v in ten.indicator_values if v.indicator_id == "btc_close") == "130"
    assert {record.entry_condition for record in trace.records} == {EntryConditionOutcome.MATCHED}


def test_missing_gapped_or_unbound_reference_data_fails_closed() -> None:
    """Research rejects absent, gapped, undeclared, or unbound reference inputs."""
    strategy = reference_strategy()
    run = reference_run(strategy, starts_at=_EVALUATION_START, hours=_HOURS)
    with pytest.raises(SignalEvaluationError, match="Candles are required for reference"):
        evaluate_signal_trace(run, strategy, _decision_candles())
    gapped = daily_bars(_FIRST_DAY, "100", "100", "90", "200")
    with pytest.raises(SignalEvaluationError, match="incomplete or not contiguous"):
        evaluate_signal_trace(
            run, strategy, _decision_candles(), reference_candles={"btc": gapped[:2] + gapped[3:]}
        )
    with pytest.raises(SignalEvaluationError, match="undeclared reference"):
        evaluate_signal_trace(
            run, strategy, _decision_candles(), reference_candles={**_references(), "sol": gapped}
        )
    unbound = reference_run(strategy, starts_at=_EVALUATION_START, hours=_HOURS, bound=False)
    with pytest.raises(SignalEvaluationError, match="reference-instrument datasets do not match"):
        evaluate_signal_trace(
            unbound, strategy, _decision_candles(), reference_candles=_references()
        )


def test_lenient_rows_skip_a_reference_without_coverage() -> None:
    """Paper/live (``strict=False``) yield no rows instead of raising."""
    strategy = reference_strategy()
    rows = calculate_reference_indicator_rows(
        strategy,
        {},
        evaluation_starts_at=_EVALUATION_START,
        evaluation_ends_at=_EVALUATION_START + timedelta(hours=1),
        strict=False,
    )
    assert rows == {}


def test_run_specification_canonical_bytes_omit_empty_reference_bindings() -> None:
    """Runs without references keep their identity; bound references join it."""
    strategy = reference_strategy()
    bound = reference_run(strategy, starts_at=_EVALUATION_START, hours=_HOURS)
    unbound = bound.model_copy(update={"reference_dataset_fingerprints": ()})
    unbound_bytes = canonical_research_run_bytes(unbound)
    assert b"reference_dataset_fingerprints" not in unbound_bytes
    assert ResearchRunSpecification.model_validate_json(unbound_bytes) == unbound
    bound_bytes = canonical_research_run_bytes(bound)
    assert b'"reference_dataset_fingerprints":[{"dataset_fingerprint"' in bound_bytes
    assert research_run_fingerprint(bound) != research_run_fingerprint(unbound)


def test_run_specification_rejects_duplicate_reference_bindings() -> None:
    """One binding per reference id and per series."""
    strategy = reference_strategy()
    run = reference_run(strategy, starts_at=_EVALUATION_START, hours=_HOURS)
    binding = run.reference_dataset_fingerprints[0]
    other = ReferenceInstrumentDataset(
        reference_id="btc2",
        product_id=binding.product_id,
        timeframe=binding.timeframe,
        dataset_fingerprint=binding.dataset_fingerprint,
    )
    payload = run.model_dump(mode="python")
    with pytest.raises(ValueError, match="reference_id values must be unique"):
        ResearchRunSpecification.model_validate(
            {**payload, "reference_dataset_fingerprints": (binding, binding)}
        )
    with pytest.raises(ValueError, match="must not repeat one series"):
        ResearchRunSpecification.model_validate(
            {**payload, "reference_dataset_fingerprints": (binding, other)}
        )
