"""Tests for deterministic completed-candle signal evaluation."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, localcontext
import json
from pathlib import Path
from typing import cast
from uuid import UUID

from pydantic import ValidationError
import pytest

from thytrader.market_data.models import Candle
from thytrader.research.indicators import calculate_indicator_rows
from thytrader.research.models import (
    BarExecutionAssumptions,
    CapitalAssumptions,
    CostAssumptions,
    EvaluationWindow,
    ResearchRunSpecification,
    WarmupWindow,
)
from thytrader.research.signal_evaluator import SignalEvaluationError, evaluate_signal_trace
from thytrader.research.trace import (
    IndicatorTraceValue,
    SignalTraceRecord,
    canonical_signal_trace_bytes,
    signal_trace_fingerprint,
)
from thytrader.strategies.models import (
    IndicatorDefinition,
    IndicatorKind,
    IndicatorParameters,
    StrategyDefinition,
    strategy_fingerprint,
)


def _strategy() -> StrategyDefinition:
    """Return a minimal published strategy that still satisfies the canonical profile."""
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
                    "right": {"literal": "2.75"},
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
    """Return an executable run request for two evaluation candles."""
    starts_at = datetime(2026, 7, 10, 2, tzinfo=UTC)
    return ResearchRunSpecification(
        schema_version="1.0",
        run_id=UUID("019faf76-6600-7000-8000-000000000065"),
        created_at=datetime(2026, 7, 29, 20, tzinfo=UTC),
        strategy_fingerprint=strategy_fingerprint(strategy),
        dataset_fingerprint="sha256:" + "2" * 64,
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


def _candles() -> tuple[Candle, ...]:
    """Return warmup, evaluation, and one unused next-open candle."""
    start = datetime(2026, 7, 10, tzinfo=UTC)
    closes = ("1", "2", "4", "1", "999")
    return tuple(
        Candle(
            starts_at=start + timedelta(hours=index),
            open=Decimal(close),
            high=Decimal(close) + Decimal("1"),
            low=Decimal(close) - Decimal("0.5"),
            close=Decimal(close),
            volume=Decimal("10"),
        )
        for index, close in enumerate(closes)
    )


def test_evaluator_emits_only_deterministic_evaluation_window_records() -> None:
    """Warmup advances state while future fill data never becomes a signal record."""
    strategy = _strategy()
    trace = evaluate_signal_trace(_run(strategy), strategy, _candles())

    assert [record.candle_starts_at for record in trace.records] == [
        datetime(2026, 7, 10, 2, tzinfo=UTC),
        datetime(2026, 7, 10, 3, tzinfo=UTC),
    ]
    assert [record.entry_condition for record in trace.records] == ["matched", "not_matched"]
    assert [record.indicator_values[0].value for record in trace.records] == ["3", "2.5"]


def test_ema_uses_sma_seed_then_exact_recursive_smoothing() -> None:
    """EMA becomes defined at one full period and then applies the ordered recurrence."""
    indicator = IndicatorDefinition(
        id="ema",
        kind=IndicatorKind.EMA,
        input="close",
        parameters=IndicatorParameters(period=3),
    )
    start = datetime(2026, 7, 10, tzinfo=UTC)
    candles = tuple(
        Candle(
            starts_at=start + timedelta(hours=index),
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=Decimal("1"),
        )
        for index, close in enumerate(("1", "2", "3", "4"))
    )

    rows = calculate_indicator_rows((indicator,), candles)

    assert [row["ema"] for row in rows] == [None, None, Decimal("2"), Decimal("3")]


def test_rsi_uses_wilder_seed_and_requires_period_price_changes() -> None:
    """RSI starts after period deltas and applies Wilder gain/loss smoothing."""
    indicator = IndicatorDefinition(
        id="rsi",
        kind=IndicatorKind.RSI,
        input="close",
        parameters=IndicatorParameters(period=2),
    )
    start = datetime(2026, 7, 10, tzinfo=UTC)
    candles = tuple(
        Candle(
            starts_at=start + timedelta(hours=index),
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=Decimal("1"),
        )
        for index, close in enumerate(("1", "2", "3", "2"))
    )

    rows = calculate_indicator_rows((indicator,), candles)

    assert [row["rsi"] for row in rows] == [
        None,
        None,
        Decimal("100"),
        Decimal("50"),
    ]

    flat = tuple(
        Candle(
            starts_at=start + timedelta(hours=index),
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=Decimal("1"),
        )
        for index in range(3)
    )
    flat_rows = calculate_indicator_rows((indicator,), flat)
    assert flat_rows[-1]["rsi"] == Decimal("50")

    recursive = tuple(
        Candle(
            starts_at=start + timedelta(hours=index),
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=Decimal("1"),
        )
        for index, close in enumerate(("1", "2", "1", "1", "2"))
    )
    recursive_indicator = IndicatorDefinition(
        id="rsi",
        kind=IndicatorKind.RSI,
        input="close",
        parameters=IndicatorParameters(period=3),
    )
    assert calculate_indicator_rows((recursive_indicator,), recursive)[-1]["rsi"] == Decimal(
        "71.42857142857142857142857142857142857142857142857142857142857144"
    )


def test_atr_and_volume_sma_have_explicit_initialization_vectors() -> None:
    """ATR uses Wilder true ranges while volume SMA uses one complete rolling window."""
    indicators = (
        IndicatorDefinition(
            id="atr",
            kind=IndicatorKind.ATR,
            input=("high", "low", "close"),
            parameters=IndicatorParameters(period=2),
        ),
        IndicatorDefinition(
            id="volume",
            kind=IndicatorKind.VOLUME_SMA,
            input="volume",
            parameters=IndicatorParameters(period=2),
        ),
    )
    start = datetime(2026, 7, 10, tzinfo=UTC)
    candles = (
        Candle(start, Decimal("1"), Decimal("2"), Decimal("0"), Decimal("1"), Decimal("2")),
        Candle(
            start + timedelta(hours=1),
            Decimal("3"),
            Decimal("4"),
            Decimal("1"),
            Decimal("3"),
            Decimal("4"),
        ),
        Candle(
            start + timedelta(hours=2),
            Decimal("5"),
            Decimal("6"),
            Decimal("4"),
            Decimal("5"),
            Decimal("8"),
        ),
    )

    rows = calculate_indicator_rows(indicators, candles)

    assert [row["atr"] for row in rows] == [None, Decimal("2.5"), Decimal("2.75")]
    assert [row["volume"] for row in rows] == [None, Decimal("3"), Decimal("6")]


def test_engine_decimal_results_ignore_ambient_decimal_precision() -> None:
    """Process-level Decimal settings must not alter deterministic trace bytes."""
    strategy = _strategy()
    run = _run(strategy)
    baseline = evaluate_signal_trace(run, strategy, _candles())

    with localcontext() as context:
        context.prec = 6
        changed_context = evaluate_signal_trace(run, strategy, _candles())

    assert canonical_signal_trace_bytes(changed_context) == canonical_signal_trace_bytes(baseline)
    assert signal_trace_fingerprint(changed_context) == signal_trace_fingerprint(baseline)


def test_ema_preserves_literal_decimal64_recurrence_beyond_default_precision() -> None:
    """A greater-than-28-digit vector pins context precision and EMA operation order."""
    closes = (
        "1.12345678901234567890123456789012345678901234567890123456789",
        "2.98765432109876543210987654321098765432109876543210987654321",
        "4.11111111111111111111111111111111111111111111111111111111111",
    )
    start = datetime(2026, 7, 10, tzinfo=UTC)
    candles = tuple(
        Candle(
            starts_at=start + timedelta(hours=index),
            open=Decimal(close),
            high=Decimal("10"),
            low=Decimal("0.1"),
            close=Decimal(close),
            volume=Decimal("1"),
        )
        for index, close in enumerate(closes)
    )
    indicator = IndicatorDefinition(
        id="ema",
        kind=IndicatorKind.EMA,
        input="close",
        parameters=IndicatorParameters(period=2),
    )
    rows = calculate_indicator_rows((indicator,), candles)

    assert [row["ema"] for row in rows] == [
        None,
        Decimal("2.05555555505555555550555555555055555555505555555550555555555"),
        Decimal("3.42592592575925925924259259259092592592575925925924259259259"),
    ]


def test_indicator_seeds_use_chronological_left_fold_decimal64_sums() -> None:
    """Non-associative vectors pin chronological accumulation for every seed family."""
    start = datetime(2026, 7, 10, tzinfo=UTC)
    values = tuple(Decimal(value) for value in ("1E64", "4", "4"))
    close_candles = tuple(
        Candle(start + timedelta(hours=index), value, value, value, value, Decimal("1"))
        for index, value in enumerate(values)
    )
    average = Decimal("3333333333333333333333333333333333333333333333333333333333333333")
    sma = IndicatorDefinition(
        id="sma",
        kind=IndicatorKind.SMA,
        input="close",
        parameters=IndicatorParameters(period=3),
    )
    ema = IndicatorDefinition(
        id="ema",
        kind=IndicatorKind.EMA,
        input="close",
        parameters=IndicatorParameters(period=3),
    )
    seeded = calculate_indicator_rows((sma, ema), close_candles)[-1]
    assert seeded == {"sma": average, "ema": average}

    atr = IndicatorDefinition(
        id="atr",
        kind=IndicatorKind.ATR,
        input=("high", "low", "close"),
        parameters=IndicatorParameters(period=3),
    )
    atr_candles = tuple(
        Candle(
            start + timedelta(hours=index),
            Decimal("0"),
            value,
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
        )
        for index, value in enumerate(values)
    )
    assert calculate_indicator_rows((atr,), atr_candles)[-1]["atr"] == average

    rsi = IndicatorDefinition(
        id="rsi",
        kind=IndicatorKind.RSI,
        input="close",
        parameters=IndicatorParameters(period=5),
    )
    rsi_values = tuple(
        Decimal(value)
        for value in (
            "1",
            "1E64",
            "5E63",
            f"5{('0' * 62)}4",
            f"5{('0' * 62)}4",
            f"5{('0' * 62)}8",
        )
    )
    rsi_candles = tuple(
        Candle(start + timedelta(hours=index), value, value, value, value, Decimal("1"))
        for index, value in enumerate(rsi_values)
    )
    assert calculate_indicator_rows((rsi,), rsi_candles)[-1]["rsi"] == Decimal(
        "66.66666666666666666666666666666666666666666666666666666666666667"
    )


def test_request_only_contract_cannot_be_evaluated() -> None:
    """Old immutable request identities never silently acquire executable semantics."""
    strategy = _strategy()
    request_only = ResearchRunSpecification.model_validate(
        {
            **_run(strategy).model_dump(mode="python"),
            "engine_contract_version": "thytrader-bar-v1",
        }
    )

    with pytest.raises(SignalEvaluationError, match="executable signal contract"):
        evaluate_signal_trace(request_only, strategy, _candles())


def test_future_candle_mutation_cannot_change_prior_signal_records() -> None:
    """Neither fill-lookahead nor a later evaluation candle may influence an earlier record."""
    strategy = _strategy()
    run = _run(strategy)
    candles = _candles()
    baseline = evaluate_signal_trace(run, strategy, candles)
    changed_future = (
        *candles[:-2],
        replace(
            candles[-2],
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
        ),
        replace(
            candles[-1],
            open=Decimal("500"),
            high=Decimal("501"),
            low=Decimal("499"),
            close=Decimal("500"),
        ),
    )

    changed = evaluate_signal_trace(run, strategy, changed_future)

    assert changed.records[0] == baseline.records[0]
    assert len(changed.records) == len(baseline.records) == 2

    changed_fill = (*candles[:-1], replace(candles[-1], close=Decimal("123456")))
    assert evaluate_signal_trace(run, strategy, changed_fill) == baseline

    prehistory = replace(
        candles[0],
        starts_at=candles[0].starts_at - timedelta(hours=1),
        open=Decimal("900"),
        high=Decimal("901"),
        low=Decimal("899"),
        close=Decimal("900"),
    )
    assert evaluate_signal_trace(run, strategy, (prehistory, *candles)) == baseline


def test_evaluator_rejects_non_utc_candle_representations() -> None:
    """Equivalent instants with non-UTC offsets cannot alter canonical trace bytes."""
    strategy = _strategy()
    run = _run(strategy)
    offset = timezone(timedelta(hours=-5))
    offset_candles = tuple(
        replace(candle, starts_at=candle.starts_at.astimezone(offset)) for candle in _candles()
    )

    with pytest.raises(SignalEvaluationError, match="UTC"):
        evaluate_signal_trace(run, strategy, offset_candles)

    baseline = evaluate_signal_trace(run, strategy, _candles())
    fill_with_offset = (
        *_candles()[:-1],
        replace(_candles()[-1], starts_at=_candles()[-1].starts_at.astimezone(offset)),
    )
    assert evaluate_signal_trace(run, strategy, fill_with_offset) == baseline

    ignored_prehistory = replace(
        _candles()[0],
        starts_at=(_candles()[0].starts_at - timedelta(hours=1)).astimezone(offset),
    )
    assert evaluate_signal_trace(run, strategy, (ignored_prehistory, *_candles())) == baseline


def test_trace_accepts_decimal64_subnormal_indicator_outputs() -> None:
    """Valid context subnormals remain canonical rather than leaking validation errors."""
    strategy = _strategy()
    run = _run(strategy)
    low = Decimal("1E-6143")
    high = Decimal(f"1.{('0' * 62)}1E-6143")
    candles = tuple(
        replace(candle, open=low, high=high, low=low, close=low) for candle in _candles()
    )

    trace = evaluate_signal_trace(run, strategy, candles)

    for record in trace.records:
        atr_value = record.indicator_values[1].value
        assert atr_value is not None
        assert "E" not in atr_value
        assert Decimal(atr_value) == Decimal("1E-6206")


def test_trace_rejects_values_below_decimal64_etiny() -> None:
    """Trace identity rejects decimals that the engine context cannot represent exactly."""
    below_etiny = f"0.{('0' * 6205)}11"

    with pytest.raises(ValidationError, match="Decimal envelope"):
        IndicatorTraceValue(indicator_id="atr", value=below_etiny)


def test_trace_identity_requires_strictly_increasing_records() -> None:
    """Canonical traces reject duplicate and descending candle boundaries."""
    strategy = _strategy()
    trace = evaluate_signal_trace(_run(strategy), strategy, _candles())
    payload = trace.model_dump(mode="python")
    first, second = trace.records

    payload["records"] = (second, first)
    with pytest.raises(ValidationError, match="strictly increasing"):
        type(trace).model_validate(payload)

    payload["records"] = (first, first)
    with pytest.raises(ValidationError, match="strictly increasing"):
        type(trace).model_validate(payload)


def test_trace_identity_requires_nonempty_unique_indicator_evidence() -> None:
    """Public trace models reject empty or ambiguous canonical evidence collections."""
    strategy = _strategy()
    trace = evaluate_signal_trace(_run(strategy), strategy, _candles())
    record_payload = trace.records[0].model_dump(mode="python")

    record_payload["indicator_values"] = ()
    with pytest.raises(ValidationError):
        SignalTraceRecord.model_validate(record_payload)

    indicator = trace.records[0].indicator_values[0]
    record_payload["indicator_values"] = (indicator, indicator)
    with pytest.raises(ValidationError, match="unique"):
        SignalTraceRecord.model_validate(record_payload)

    trace_payload = trace.model_dump(mode="python")
    trace_payload["records"] = ()
    with pytest.raises(ValidationError):
        type(trace).model_validate(trace_payload)


@pytest.mark.parametrize(
    "indicator_ids",
    [
        ("sma",),
        ("sma", "atr", "extra"),
        ("atr", "sma"),
    ],
)
def test_trace_identity_matches_exact_declared_indicator_sequence(
    indicator_ids: tuple[str, ...],
) -> None:
    """Canonical trace records match the exact identity-bearing indicator declaration order."""
    strategy = _strategy()
    trace = evaluate_signal_trace(_run(strategy), strategy, _candles())
    payload = trace.model_dump(mode="python")
    payload["indicator_ids"] = indicator_ids

    with pytest.raises(ValidationError, match="indicator sequence"):
        type(trace).model_validate(payload)


def test_trace_identity_helpers_revalidate_copied_models() -> None:
    """Canonical helpers reject typed instances forged through unchecked model copies."""
    strategy = _strategy()
    trace = evaluate_signal_trace(_run(strategy), strategy, _candles())
    first = trace.records[0]
    extra = first.indicator_values[0].model_copy(update={"indicator_id": "extra"})
    forged_vectors = (
        (),
        first.indicator_values[:-1],
        (*first.indicator_values, extra),
        (first.indicator_values[0], first.indicator_values[0]),
        tuple(reversed(first.indicator_values)),
    )
    forged_traces = [trace.model_copy(update={"records": ()})]
    forged_traces.extend(
        trace.model_copy(
            update={
                "records": (
                    first.model_copy(update={"indicator_values": vector}),
                    *trace.records[1:],
                )
            }
        )
        for vector in forged_vectors
    )

    for forged_trace in forged_traces:
        with pytest.raises(ValidationError):
            canonical_signal_trace_bytes(forged_trace)
        with pytest.raises(ValidationError):
            signal_trace_fingerprint(forged_trace)


def test_evaluator_revalidates_copied_run_and_strategy_models() -> None:
    """Evaluation fails closed when unchecked model copies violate published input contracts."""
    strategy = _strategy()
    run = _run(strategy)

    with pytest.raises(SignalEvaluationError, match="invalid"):
        evaluate_signal_trace(run.model_copy(update={"random_seed": -1}), strategy, _candles())
    with pytest.raises(SignalEvaluationError, match="invalid"):
        evaluate_signal_trace(run, strategy.model_copy(update={"name": ""}), _candles())


def test_malformed_selected_candles_fail_through_controlled_error_boundary() -> None:
    """Runtime-invalid timestamp and OHLCV types never escape as implementation errors."""
    strategy = _strategy()
    run = _run(strategy)
    candles = _candles()
    naive_start = candles[1].starts_at.replace(tzinfo=None)

    with pytest.raises(SignalEvaluationError, match="UTC"):
        evaluate_signal_trace(
            run,
            strategy,
            (candles[0], replace(candles[1], starts_at=naive_start), *candles[2:]),
        )

    with pytest.raises(SignalEvaluationError, match="Decimal limits"):
        evaluate_signal_trace(
            run,
            strategy,
            (candles[0], replace(candles[1], open=cast("Decimal", 1.0)), *candles[2:]),
        )


def test_evaluator_rejects_nonpositive_ohlc_and_strategy_identity_mismatch() -> None:
    """Malformed market values and a different strategy fail before emitting trace evidence."""
    strategy = _strategy()
    run = _run(strategy)
    malformed = (replace(_candles()[0], low=Decimal("0")), *_candles()[1:])

    with pytest.raises(SignalEvaluationError, match="OHLCV"):
        evaluate_signal_trace(run, strategy, malformed)

    different_strategy = strategy.model_copy(update={"name": "Different immutable strategy"})
    with pytest.raises(SignalEvaluationError, match="strategy identity"):
        evaluate_signal_trace(run, different_strategy, _candles())

    overflowing = tuple(
        replace(
            candle,
            open=Decimal("9E+6144"),
            high=Decimal("9.9E+6144"),
            low=Decimal("8E+6144"),
            close=Decimal("9E+6144"),
        )
        for candle in _candles()
    )
    with pytest.raises(SignalEvaluationError, match="indicator calculation"):
        evaluate_signal_trace(run, strategy, overflowing)


def test_reference_signal_trace_matches_literal_golden_bytes() -> None:
    """The complete trace ordering and Decimal rendering must remain byte-for-byte stable."""
    strategy = _strategy()
    trace = evaluate_signal_trace(_run(strategy), strategy, _candles())
    expected = (
        Path("tests/research/golden/reference_signal_trace_v1.json").read_bytes().rstrip(b"\n")
    )

    assert canonical_signal_trace_bytes(trace) == expected
    assert signal_trace_fingerprint(trace) == (
        "sha256:db75b85d1960af31c6ff8edee84d82cb1bb209906e0bcb4549f71e4bca1870b1"
    )


def test_crossover_uses_only_previous_and_current_completed_values() -> None:
    """A crossover fires once on boundary transition rather than while series remain ordered."""
    payload = _strategy().model_dump(mode="json", by_alias=True)
    payload["data_requirements"]["warmup_bars"] = 3
    payload["indicators"] = [
        {"id": "fast", "kind": "sma", "input": "close", "parameters": {"period": 2}},
        {"id": "slow", "kind": "sma", "input": "close", "parameters": {"period": 3}},
        {
            "id": "atr",
            "kind": "atr",
            "input": ["high", "low", "close"],
            "parameters": {"period": 2},
        },
    ]
    payload["entry"]["when"] = {
        "all": [
            {
                "left": {"indicator": "fast"},
                "operator": "crosses_above",
                "right": {"indicator": "slow"},
            }
        ]
    }
    strategy = StrategyDefinition.model_validate(payload)
    run_payload = _run(strategy).model_dump(mode="python")
    run_payload["evaluation"] = {
        "starts_at": datetime(2026, 7, 10, 3, tzinfo=UTC),
        "ends_at": datetime(2026, 7, 10, 5, tzinfo=UTC),
    }
    run_payload["warmup"] = {
        "bars": 3,
        "starts_at": datetime(2026, 7, 10, tzinfo=UTC),
    }
    run = ResearchRunSpecification.model_validate(run_payload)
    start = datetime(2026, 7, 10, tzinfo=UTC)
    candles = tuple(
        Candle(
            starts_at=start + timedelta(hours=index),
            open=Decimal(close),
            high=Decimal(close) + Decimal("0.5"),
            low=Decimal(close) - Decimal("0.5"),
            close=Decimal(close),
            volume=Decimal("1"),
        )
        for index, close in enumerate(("3", "2", "1", "4", "5", "6"))
    )

    trace = evaluate_signal_trace(run, strategy, candles)

    assert [record.entry_condition for record in trace.records] == ["matched", "not_matched"]


def test_trace_identity_rejects_coercible_bytes() -> None:
    """Identity-bearing trace fields must accept native canonical strings only."""
    strategy = _strategy()
    trace = evaluate_signal_trace(_run(strategy), strategy, _candles())
    payload = trace.model_dump(mode="python")
    payload["run_fingerprint"] = trace.run_fingerprint.encode("ascii")

    with pytest.raises(ValidationError):
        type(trace).model_validate(payload)


@pytest.mark.parametrize(
    "timestamp",
    [
        datetime(2026, 1, 1),  # noqa: DTZ001 - deliberate invalid trace-model input.
        datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=-5))),
    ],
)
def test_trace_record_identity_rejects_non_utc_timestamps(timestamp: datetime) -> None:
    """Public trace records accept only timezone-aware zero-offset timestamps."""
    strategy = _strategy()
    trace = evaluate_signal_trace(_run(strategy), strategy, _candles())
    record_payload = trace.records[0].model_dump(mode="python")
    record_payload["candle_starts_at"] = timestamp

    with pytest.raises(ValidationError, match="UTC"):
        SignalTraceRecord.model_validate(record_payload)
