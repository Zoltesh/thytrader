"""Pure deterministic entry-condition evaluation for immutable research requests."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, DecimalException
from typing import TYPE_CHECKING, Literal

from pydantic import ValidationError

from thytrader.market_data.models import parse_candle_interval
from thytrader.research.indicators import (
    IndicatorCalculationError,
    calculate_indicator_rows,
    canonical_decimal,
)
from thytrader.research.models import (
    ResearchRunSpecification,
    research_run_fingerprint,
    specification_bar_interval,
)
from thytrader.research.multi_timeframe import (
    htf_candle_starts,
    index_candles_by_start,
    ltf_close,
    mapped_htf_start,
)
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
    decision_and_filter_indicators,
    indicator_value_keys,
    operand_value_key,
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
    htf_candles: Sequence[Candle] = (),
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
    htf_rows = _htf_indicator_rows(specification, strategy, htf_candles)
    declared = decision_and_filter_indicators(strategy)
    indicator_ids = tuple(key for indicator in declared for key in indicator_value_keys(indicator))
    records: list[SignalTraceRecord] = []
    for index, (candle, values) in enumerate(zip(engine_candles, indicator_rows, strict=True)):
        if candle.starts_at < specification.evaluation.starts_at:
            continue
        previous_values = indicator_rows[index - 1] if index else None
        ltf_outcome = _condition_outcome(strategy.entry.when, values, previous_values)
        htf_outcome, htf_values = _htf_filter_outcome(
            strategy,
            candle,
            previous_ltf_start=_previous_ltf_start(engine_candles, index),
            htf_rows=htf_rows,
        )
        outcome = _and_outcomes(ltf_outcome, htf_outcome)
        records.append(
            SignalTraceRecord(
                candle_starts_at=candle.starts_at,
                indicator_values=tuple(
                    IndicatorTraceValue(
                        indicator_id=key,
                        value=_canonical_optional(_row_value(values, htf_values, key)),
                    )
                    for key in indicator_ids
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
        indicator_ids=indicator_ids,
        records=tuple(records),
    )


def _row_value(
    values: Mapping[str, Decimal | None],
    htf_values: Mapping[str, Decimal | None],
    key: str,
) -> Decimal | None:
    """Read one LTF or HTF series key without inventing a missing output."""
    if key in values:
        return values[key]
    return htf_values.get(key)


def _canonical_optional(value: Decimal | None) -> str | None:
    """Serialize one optional indicator value with explicit static narrowing."""
    return None if value is None else canonical_decimal(value)


def _and_outcomes(
    left: EntryConditionOutcome, right: EntryConditionOutcome
) -> EntryConditionOutcome:
    """AND two tri-state outcomes without turning undefined into a match."""
    if left is EntryConditionOutcome.UNDEFINED or right is EntryConditionOutcome.UNDEFINED:
        return EntryConditionOutcome.UNDEFINED
    if left is EntryConditionOutcome.MATCHED and right is EntryConditionOutcome.MATCHED:
        return EntryConditionOutcome.MATCHED
    return EntryConditionOutcome.NOT_MATCHED


def _previous_ltf_start(engine_candles: Sequence[Candle], index: int) -> datetime | None:
    """Return the previous completed LTF bar start when it exists in the engine window."""
    if index <= 0:
        return None
    return engine_candles[index - 1].starts_at


def _htf_indicator_rows(
    specification: ResearchRunSpecification,
    strategy: StrategyDefinition,
    htf_candles: Sequence[Candle],
) -> dict[datetime, Mapping[str, Decimal | None]]:
    """Calculate HTF indicators on required closed HTF bars, or an empty map."""
    htf_filter = strategy.htf_filter
    if htf_filter is None:
        if htf_candles:
            raise SignalEvaluationError("HTF candles were supplied without an HTF filter.")
        if specification.htf_dataset_fingerprint is not None:
            raise SignalEvaluationError(
                "Research run HTF dataset is not declared by the published strategy."
            )
        return {}
    if specification.htf_dataset_fingerprint is None:
        raise SignalEvaluationError(
            "Research run HTF dataset fingerprint is required for an HTF-filter strategy."
        )
    if not htf_candles:
        raise SignalEvaluationError("HTF candles are required for an HTF-filter strategy.")
    expected_starts = htf_candle_starts(
        evaluation_starts_at=specification.evaluation.starts_at,
        evaluation_ends_at=specification.evaluation.ends_at,
        htf_filter=htf_filter,
    )
    selected = _required_htf_candles(expected_starts, htf_candles)
    try:
        rows = calculate_indicator_rows(htf_filter.indicators, selected)
    except (DecimalException, IndicatorCalculationError) as error:
        raise SignalEvaluationError(
            "HTF indicator calculation failed under the deterministic Decimal contract."
        ) from error
    return {candle.starts_at: values for candle, values in zip(selected, rows, strict=True)}


def _required_htf_candles(
    expected_starts: Sequence[datetime],
    candles: Sequence[Candle],
) -> tuple[Candle, ...]:
    """Select exact closed HTF bars and reject duplicates, gaps, or malformed bars."""
    try:
        by_start = index_candles_by_start(candles)
    except ValueError as error:
        raise SignalEvaluationError(str(error)) from error
    selected: list[Candle] = []
    for start in expected_starts:
        candle = by_start.get(start)
        if candle is None:
            raise SignalEvaluationError("HTF candle coverage is incomplete or not contiguous.")
        _require_ohlcv_contract(candle)
        selected.append(candle)
    return tuple(selected)


def _htf_filter_outcome(
    strategy: StrategyDefinition,
    candle: Candle,
    previous_ltf_start: datetime | None,
    htf_rows: Mapping[datetime, Mapping[str, Decimal | None]],
) -> tuple[EntryConditionOutcome, Mapping[str, Decimal | None]]:
    """Evaluate the HTF filter from last completed HTF values held onto this LTF close."""
    htf_filter = strategy.htf_filter
    if htf_filter is None:
        return EntryConditionOutcome.MATCHED, {}
    current_close = ltf_close(candle.starts_at, strategy.timeframe)
    current_start = mapped_htf_start(current_close, htf_filter.timeframe)
    current_values = htf_rows.get(current_start)
    if current_values is None:
        raise SignalEvaluationError("HTF alignment missed a last completed HTF bar.")
    if previous_ltf_start is None:
        previous_values = None
    else:
        previous_close = ltf_close(previous_ltf_start, strategy.timeframe)
        previous_start = mapped_htf_start(previous_close, htf_filter.timeframe)
        previous_values = htf_rows.get(previous_start)
        if previous_values is None:
            raise SignalEvaluationError("HTF alignment missed a previous completed HTF bar.")
    outcome = _condition_outcome(htf_filter.when, current_values, previous_values)
    return outcome, current_values


def _require_ohlcv_contract(candle: Candle) -> None:
    """Reject one candle that violates the evaluator's OHLCV Decimal contract."""
    values = (candle.open, candle.high, candle.low, candle.close, candle.volume)
    if any(not _within_decimal_contract(value) for value in values):
        raise SignalEvaluationError("Signal evaluation candles violate OHLCV Decimal limits.")
    if _invalid_ohlc_geometry(candle):
        raise SignalEvaluationError("Signal evaluation candles violate OHLCV invariants.")


def _invalid_ohlc_geometry(candle: Candle) -> bool:
    """Return whether OHLC ordering or sign invariants fail."""
    if candle.open <= 0 or candle.high <= 0 or candle.low <= 0 or candle.close <= 0:
        return True
    if candle.low > candle.high or candle.volume < 0:
        return True
    if candle.open < candle.low or candle.open > candle.high:
        return True
    return candle.close < candle.low or candle.close > candle.high


def _verify_contract(
    specification: ResearchRunSpecification,
    strategy: StrategyDefinition,
) -> Literal[
    "thytrader-bar-signal-v1",
    "thytrader-bar-backtest-v1",
    "thytrader-bar-backtest-v2",
    "thytrader-bar-backtest-v3",
]:
    """Require an executable engine contract and immutable strategy identity."""
    engine_contract_version = specification.engine_contract_version
    if engine_contract_version == "thytrader-bar-v1":
        raise SignalEvaluationError("Research run does not select the executable signal contract.")
    if engine_contract_version not in {
        "thytrader-bar-signal-v1",
        "thytrader-bar-backtest-v1",
        "thytrader-bar-backtest-v2",
        "thytrader-bar-backtest-v3",
    }:
        raise AssertionError("Research run engine contract literal is invalid.")
    if strategy_fingerprint(strategy) != specification.strategy_fingerprint:
        raise SignalEvaluationError("Research run strategy identity failed verification.")
    if specification.warmup.bars != strategy.data_requirements.warmup_bars:
        raise SignalEvaluationError("Research run warmup does not match the strategy requirement.")
    if specification_bar_interval(specification) is not parse_candle_interval(strategy.timeframe):
        raise SignalEvaluationError(
            "Research run bar spacing does not match the published strategy timeframe."
        )
    has_htf_filter = strategy.htf_filter is not None
    has_htf_dataset = specification.htf_dataset_fingerprint is not None
    if has_htf_filter != has_htf_dataset:
        raise SignalEvaluationError(
            "Research run HTF dataset identity does not match the published strategy."
        )
    return engine_contract_version


def _required_candles(
    specification: ResearchRunSpecification,
    candles: Sequence[Candle],
) -> tuple[Candle, ...]:
    """Select exact warmup/evaluation candles and reject duplicates, gaps, or malformed bars."""
    selected = _select_window_candles(specification, candles)
    bar = specification_bar_interval(specification).duration
    expected_count = specification.warmup.bars + int(
        (specification.evaluation.ends_at - specification.evaluation.starts_at) / bar
    )
    if len(selected) != expected_count:
        raise SignalEvaluationError(
            "Signal evaluation candle coverage is incomplete or duplicated."
        )
    for index, candle in enumerate(selected):
        expected_start = specification.warmup.starts_at + bar * index
        if candle.starts_at != expected_start:
            raise SignalEvaluationError("Signal evaluation candles are not contiguous UTC bars.")
        _require_ohlcv_contract(candle)
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


def entry_condition_outcome(
    condition: ConditionNode,
    current: Mapping[str, Decimal | None],
    previous: Mapping[str, Decimal | None] | None,
) -> EntryConditionOutcome:
    """Map tri-state condition evaluation into one explicit auditable outcome."""
    return _condition_outcome(condition, current, previous)


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
        return values.get(operand_value_key(operand))
    if isinstance(operand, LiteralOperand):
        return Decimal(operand.literal)
    raise SignalEvaluationError("Signal condition contains an unsupported operand.")
