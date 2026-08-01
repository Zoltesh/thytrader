"""Pure deterministic entry-condition evaluation for immutable research requests."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, DecimalException
from typing import TYPE_CHECKING, Literal

from pydantic import ValidationError

from thytrader.research.indicators import (
    IndicatorCalculationError,
    calculate_indicator_rows,
    canonical_decimal,
)
from thytrader.research.models import ResearchRunSpecification, research_run_fingerprint
from thytrader.research.trace import (
    EntryConditionOutcome,
    IndicatorTraceValue,
    SignalTrace,
    SignalTraceRecord,
)
from thytrader.strategies.models import (
    AllCondition,
    ComparisonCondition,
    ComparisonOperator,
    IndicatorOperand,
    LiteralOperand,
    NotCondition,
    StrategyDefinition,
    strategy_fingerprint,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import ConditionNode, ConditionOperand


class SignalEvaluationError(ValueError):
    """Report a fail-closed signal-contract, identity, or candle-range violation."""


def evaluate_signal_trace(
    specification: ResearchRunSpecification,
    strategy: StrategyDefinition,
    candles: Sequence[Candle],
) -> SignalTrace:
    """Evaluate deterministic entry conditions over one exact completed-candle interval."""
    try:
        specification = ResearchRunSpecification.model_validate(
            specification.model_dump(mode="python")
        )
        strategy = StrategyDefinition.model_validate(
            strategy.model_dump(mode="python", by_alias=True)
        )
    except ValidationError as error:
        raise SignalEvaluationError("Signal evaluation inputs are invalid.") from error
    engine_contract_version = _verify_contract(specification, strategy)
    engine_candles = _required_candles(specification, candles)
    try:
        indicator_rows = calculate_indicator_rows(strategy.indicators, engine_candles)
    except (DecimalException, IndicatorCalculationError) as error:
        raise SignalEvaluationError(
            "Signal indicator calculation failed under the deterministic Decimal contract."
        ) from error
    records: list[SignalTraceRecord] = []
    for index, (candle, values) in enumerate(zip(engine_candles, indicator_rows, strict=True)):
        if candle.starts_at < specification.evaluation.starts_at:
            continue
        previous_values = indicator_rows[index - 1] if index else None
        outcome = _condition_outcome(strategy.entry.when, values, previous_values)
        records.append(
            SignalTraceRecord(
                candle_starts_at=candle.starts_at,
                indicator_values=tuple(
                    IndicatorTraceValue(
                        indicator_id=indicator.id,
                        value=_canonical_optional(values[indicator.id]),
                    )
                    for indicator in strategy.indicators
                ),
                entry_condition=outcome,
            )
        )
    return SignalTrace(
        schema_version="1.0",
        run_fingerprint=research_run_fingerprint(specification),
        strategy_fingerprint=specification.strategy_fingerprint,
        dataset_fingerprint=specification.dataset_fingerprint,
        engine_contract_version=engine_contract_version,
        indicator_ids=tuple(indicator.id for indicator in strategy.indicators),
        records=tuple(records),
    )


def _canonical_optional(value: Decimal | None) -> str | None:
    """Serialize one optional indicator value with explicit static narrowing."""
    return None if value is None else canonical_decimal(value)


def _verify_contract(
    specification: ResearchRunSpecification,
    strategy: StrategyDefinition,
) -> Literal["thytrader-bar-signal-v1", "thytrader-bar-backtest-v1"]:
    """Require an executable engine contract and immutable strategy identity."""
    engine_contract_version = specification.engine_contract_version
    if engine_contract_version == "thytrader-bar-v1":
        raise SignalEvaluationError("Research run does not select the executable signal contract.")
    if engine_contract_version not in {
        "thytrader-bar-signal-v1",
        "thytrader-bar-backtest-v1",
    }:
        raise AssertionError("Research run engine contract literal is invalid.")
    if strategy_fingerprint(strategy) != specification.strategy_fingerprint:
        raise SignalEvaluationError("Research run strategy identity failed verification.")
    if specification.warmup.bars != strategy.data_requirements.warmup_bars:
        raise SignalEvaluationError("Research run warmup does not match the strategy requirement.")
    return engine_contract_version


def _required_candles(
    specification: ResearchRunSpecification,
    candles: Sequence[Candle],
) -> tuple[Candle, ...]:
    """Select exact warmup/evaluation candles and reject duplicates, gaps, or malformed bars."""
    selected = _select_window_candles(specification, candles)
    expected_count = specification.warmup.bars + int(
        (specification.evaluation.ends_at - specification.evaluation.starts_at) / timedelta(hours=1)
    )
    if len(selected) != expected_count:
        raise SignalEvaluationError(
            "Signal evaluation candle coverage is incomplete or duplicated."
        )
    for index, candle in enumerate(selected):
        expected_start = specification.warmup.starts_at + timedelta(hours=index)
        if candle.starts_at != expected_start:
            raise SignalEvaluationError(
                "Signal evaluation candles are not contiguous hourly UTC bars."
            )
        values = (candle.open, candle.high, candle.low, candle.close, candle.volume)
        if any(not _within_decimal_contract(value) for value in values):
            raise SignalEvaluationError("Signal evaluation candles violate OHLCV Decimal limits.")
        if (
            candle.open <= 0
            or candle.high <= 0
            or candle.low <= 0
            or candle.close <= 0
            or candle.low > candle.high
            or candle.open < candle.low
            or candle.open > candle.high
            or candle.close < candle.low
            or candle.close > candle.high
            or candle.volume < 0
        ):
            raise SignalEvaluationError("Signal evaluation candles violate OHLCV invariants.")
    return selected


def _select_window_candles(
    specification: ResearchRunSpecification,
    candles: Sequence[Candle],
) -> tuple[Candle, ...]:
    """Classify candle timestamps without leaking malformed runtime representations."""
    selected_candles: list[Candle] = []
    warmup_start = specification.warmup.starts_at
    evaluation_end = specification.evaluation.ends_at
    warmup_naive = warmup_start.replace(tzinfo=None)
    evaluation_end_naive = evaluation_end.replace(tzinfo=None)
    for candle in candles:
        starts_at = candle.starts_at
        if not isinstance(starts_at, datetime):
            raise SignalEvaluationError("Signal evaluation candles have invalid timestamps.")
        try:
            offset = starts_at.utcoffset()
        except (TypeError, ValueError, OverflowError) as error:
            raise SignalEvaluationError(
                "Signal evaluation candles have invalid timestamps."
            ) from error
        if offset is None:
            if warmup_naive <= starts_at < evaluation_end_naive:
                raise SignalEvaluationError("Signal evaluation candles must use UTC timestamps.")
            continue
        try:
            required = warmup_start <= starts_at < evaluation_end
        except (TypeError, ValueError, OverflowError) as error:
            raise SignalEvaluationError(
                "Signal evaluation candles have invalid timestamps."
            ) from error
        if required:
            if offset != timedelta(0):
                raise SignalEvaluationError("Signal evaluation candles must use UTC timestamps.")
            selected_candles.append(candle)
    return tuple(selected_candles)


def _within_decimal_contract(value: object) -> bool:
    """Return whether one exact value fits the evaluator's finite Decimal envelope."""
    return (
        isinstance(value, Decimal)
        and value.is_finite()
        and len(value.as_tuple().digits) <= 64
        and -6143 <= value.adjusted() <= 6144
    )


def _condition_outcome(
    condition: ConditionNode,
    current: Mapping[str, Decimal | None],
    previous: Mapping[str, Decimal | None] | None,
) -> EntryConditionOutcome:
    """Map tri-state condition evaluation into one explicit auditable outcome."""
    result = _evaluate_condition(condition, current, previous)
    if result is None:
        return EntryConditionOutcome.UNDEFINED
    return EntryConditionOutcome.MATCHED if result else EntryConditionOutcome.NOT_MATCHED


def _evaluate_condition(
    condition: ConditionNode,
    current: Mapping[str, Decimal | None],
    previous: Mapping[str, Decimal | None] | None,
) -> bool | None:
    """Evaluate recursive conditions without short-circuiting undefined indicator state."""
    if isinstance(condition, ComparisonCondition):
        return _evaluate_comparison(condition, current, previous)
    if isinstance(condition, NotCondition):
        child = _evaluate_condition(condition.not_, current, previous)
        return None if child is None else not child
    children = condition.all if isinstance(condition, AllCondition) else condition.any
    outcomes = tuple(_evaluate_condition(child, current, previous) for child in children)
    if any(outcome is None for outcome in outcomes):
        return None
    defined = tuple(bool(outcome) for outcome in outcomes)
    return all(defined) if isinstance(condition, AllCondition) else any(defined)


def _evaluate_comparison(
    condition: ComparisonCondition,
    current: Mapping[str, Decimal | None],
    previous: Mapping[str, Decimal | None] | None,
) -> bool | None:
    """Evaluate one typed exact comparison, including two-completed-bar crossovers."""
    left = _operand_value(condition.left, current)
    right = _operand_value(condition.right, current)
    if left is None or right is None:
        return None
    operator = condition.operator
    if operator is ComparisonOperator.GT:
        return left > right
    if operator is ComparisonOperator.GTE:
        return left >= right
    if operator is ComparisonOperator.LT:
        return left < right
    if operator is ComparisonOperator.LTE:
        return left <= right
    if operator is ComparisonOperator.EQ:
        return left == right
    if previous is None:
        return None
    previous_left = _operand_value(condition.left, previous)
    previous_right = _operand_value(condition.right, previous)
    if previous_left is None or previous_right is None:
        return None
    if operator is ComparisonOperator.CROSSES_ABOVE:
        return previous_left <= previous_right and left > right
    return previous_left >= previous_right and left < right


def _operand_value(
    operand: ConditionOperand,
    values: Mapping[str, Decimal | None],
) -> Decimal | None:
    """Resolve one indicator or exact literal operand for a single completed candle."""
    if isinstance(operand, IndicatorOperand):
        return values.get(operand.indicator)
    if isinstance(operand, LiteralOperand):
        return Decimal(operand.literal)
    raise SignalEvaluationError("Signal condition contains an unsupported operand.")
