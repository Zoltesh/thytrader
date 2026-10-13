"""Differential comparison of the actual research and deployed signal-evaluation paths."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thytrader.decimal_text import canonical_decimal
from thytrader.evaluation.models import (
    CapitalAssumptions,
    CostAssumptions,
    EvaluationWindow,
    IndicatorTimeframeDataset,
    ReferenceInstrumentDataset,
    ResearchRunSpecification,
    WarmupWindow,
)
from thytrader.evaluation.signal_evaluator import evaluate_signal_trace
from thytrader.execution.signals import evaluate_latest_entry_evidence
from thytrader.market_data.models import parse_candle_interval
from thytrader.replay.models import fingerprint
from thytrader.strategies.models import strategy_fingerprint

if TYPE_CHECKING:
    from thytrader.evaluation.trace import SignalTraceRecord
    from thytrader.replay.steps import ReplayStep
    from thytrader.strategies.models import StrategyDefinition


def compare_signals(
    step: ReplayStep, strategy: StrategyDefinition
) -> tuple[SignalTraceRecord | None, bool]:
    """Execute both existing evaluators, comparing indicator values as well as entry outcomes."""
    if step.operation == "reconcile" or len(step.candles) <= strategy.data_requirements.warmup_bars:
        return None, False
    start = step.candles[-1].starts_at
    duration = parse_candle_interval(strategy.timeframe).duration
    spec = ResearchRunSpecification(
        schema_version="1.0",
        run_id=step.before.deployment.id,
        created_at=step.before.deployment.created_at,
        strategy_fingerprint=strategy_fingerprint(strategy),
        dataset_fingerprint=fingerprint(step.model_dump_json(include={"candles"}).encode()),
        htf_dataset_fingerprint=(
            fingerprint(step.model_dump_json(include={"htf_candles"}).encode())
            if strategy.htf_filter is not None
            else None
        ),
        indicator_dataset_fingerprints=tuple(
            IndicatorTimeframeDataset(
                timeframe=window.timeframe,
                dataset_fingerprint=fingerprint(window.model_dump_json().encode()),
            )
            for window in step.indicator_windows
        ),
        reference_dataset_fingerprints=tuple(
            ReferenceInstrumentDataset(
                reference_id=window.key,
                product_id=window.product_id,
                timeframe=window.timeframe,
                dataset_fingerprint=fingerprint(window.model_dump_json().encode()),
            )
            for window in step.reference_windows
        ),
        evaluation=EvaluationWindow(starts_at=start, ends_at=start + duration),
        warmup=WarmupWindow(
            bars=strategy.data_requirements.warmup_bars,
            starts_at=start - duration * strategy.data_requirements.warmup_bars,
        ),
        capital=CapitalAssumptions(
            quote_currency=strategy.instrument.quote_currency, initial_quote_balance="1"
        ),
        costs=CostAssumptions(maker_fee_rate="0", taker_fee_rate="0", fixed_slippage_bps="0"),
        random_seed=0,
    )
    extras = {window.key: window.candles for window in step.indicator_windows}
    references = {window.key: window.candles for window in step.reference_windows}
    record = evaluate_signal_trace(
        spec, strategy, step.candles, step.htf_candles, extras, reference_candles=references
    ).records[-1]
    actual = evaluate_latest_entry_evidence(
        strategy, step.candles, step.htf_candles, extras, reference_candles=references
    )
    merged = dict(actual.current) | dict(actual.htf_current)
    values = {
        key: None if value is None else canonical_decimal(value) for key, value in merged.items()
    }
    matched = record.entry_condition == actual.outcome and all(
        values.get(item.indicator_id) == item.value for item in record.indicator_values
    )
    return record, matched
