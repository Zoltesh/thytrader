"""Server-side entry-condition traces for published backtest results (ADR 0090).

``thytrader-research-evaluate`` used to read PostgreSQL and the Parquet dataset root
directly from the operator's shell. Compose installs keep both inside the API
container, so it failed for every run behind one opaque message. The trace is now
evaluated by the API, which owns the read-only dataset volume, for the exact run behind
one result. The evaluated trace must reproduce the result's ``signal_trace_fingerprint``
or the request fails closed: a trace that does not match the result explains nothing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from thytrader.evaluation.models import (
    BacktestEngine,
    FingerprintText,
)
from thytrader.evaluation.signal_evaluator import evaluate_signal_trace
from thytrader.evaluation.trace import (
    EntryConditionOutcome,
    IndicatorId,
    SignalTrace,
    SignalTraceRecord,
    signal_trace_fingerprint,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from thytrader.backtest.models import BacktestResult
    from thytrader.evaluation.models import ResearchRunSpecification
    from thytrader.market_data.models import Candle
    from thytrader.strategies.models import StrategyDefinition
    from thytrader.strategies.snapshots import StrategySnapshot

SIGNAL_TRACE_PAGE_MAX_LIMIT = 1000
SIGNAL_TRACE_PAGE_DEFAULT_LIMIT = 200
TraceOutcomeFilter = Literal["all", "matched", "not_matched", "undefined"]


class SignalTraceMismatchError(RuntimeError):
    """The re-evaluated trace does not reproduce the result's recorded trace identity."""


class VerifiedCandleReader(Protocol):
    """Read exact verified candles by immutable dataset identity."""

    def load_candles(self, content_fingerprint: str) -> tuple[Candle, ...]:
        """Load and reverify one exact immutable candle dataset."""
        ...


class StrategySnapshotReader(Protocol):
    """Load one verified strategy snapshot by fingerprint."""

    async def load(self, strategy_fingerprint_value: str) -> StrategySnapshot:
        """Load and verify one exact snapshot."""
        ...


@dataclass(frozen=True, slots=True)
class EvaluatedResultTrace:
    """A verified re-evaluated trace and the primary product it describes."""

    trace: SignalTrace
    product_id: str


class SignalTraceCounts(BaseModel):
    """How many evaluation bars each entry-condition outcome covered."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    matched: int = Field(ge=0)
    not_matched: int = Field(ge=0)
    undefined: int = Field(ge=0)


class SignalTracePage(BaseModel):
    """One bounded page of a result's primary-product entry-condition trace."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    result_fingerprint: FingerprintText
    run_fingerprint: FingerprintText
    strategy_fingerprint: FingerprintText
    dataset_fingerprint: FingerprintText
    signal_trace_fingerprint: FingerprintText
    engine: BacktestEngine
    product_id: str
    indicator_ids: tuple[IndicatorId, ...]
    total_records: int = Field(ge=0)
    counts: SignalTraceCounts
    outcome: TraceOutcomeFilter
    limit: int = Field(ge=1, le=SIGNAL_TRACE_PAGE_MAX_LIMIT)
    offset: int = Field(ge=0)
    returned: int = Field(ge=0)
    records: tuple[SignalTraceRecord, ...]
    next_cursor: str | None = None


async def evaluate_result_signal_trace(
    result: BacktestResult,
    *,
    specification: ResearchRunSpecification,
    strategy_store: StrategySnapshotReader,
    dataset_store: VerifiedCandleReader,
) -> EvaluatedResultTrace:
    """Re-evaluate the primary product's trace for one result and verify its identity."""
    snapshot = await strategy_store.load(result.strategy_fingerprint)
    definition = snapshot.definition
    trace = await asyncio.to_thread(
        _evaluate_primary_trace, specification, definition, dataset_store
    )
    if signal_trace_fingerprint(trace) != result.signal_trace_fingerprint:
        raise SignalTraceMismatchError(
            "The re-evaluated signal trace does not match the result's recorded trace."
        )
    return EvaluatedResultTrace(trace=trace, product_id=definition.instrument.product_id)


def _evaluate_primary_trace(
    specification: ResearchRunSpecification,
    definition: StrategyDefinition,
    dataset_store: VerifiedCandleReader,
) -> SignalTrace:
    """Load the run's verified datasets and evaluate the primary product off the loop."""
    candles = dataset_store.load_candles(specification.dataset_fingerprint)
    htf_candles: tuple[Candle, ...] = ()
    if specification.htf_dataset_fingerprint is not None:
        htf_candles = dataset_store.load_candles(specification.htf_dataset_fingerprint)
    extra_candles: dict[str, tuple[Candle, ...]] = {
        item.timeframe: dataset_store.load_candles(item.dataset_fingerprint)
        for item in specification.indicator_dataset_fingerprints
    }
    reference_candles = {
        item.reference_id: dataset_store.load_candles(item.dataset_fingerprint)
        for item in specification.reference_dataset_fingerprints
    }
    return evaluate_signal_trace(
        specification,
        definition,
        candles,
        htf_candles,
        extra_candles,
        reference_candles=reference_candles,
    )


def signal_trace_page(
    trace: SignalTrace,
    *,
    result_fingerprint: str,
    product_id: str,
    outcome: TraceOutcomeFilter,
    limit: int,
    offset: int,
    next_cursor_for: Callable[[int], str],
) -> SignalTracePage:
    """Filter one verified trace by outcome and return one bounded page of records."""
    records = trace.records
    counts = SignalTraceCounts(
        matched=_count(records, EntryConditionOutcome.MATCHED),
        not_matched=_count(records, EntryConditionOutcome.NOT_MATCHED),
        undefined=_count(records, EntryConditionOutcome.UNDEFINED),
    )
    selected = (
        records
        if outcome == "all"
        else tuple(item for item in records if item.entry_condition.value == outcome)
    )
    page = selected[offset : offset + limit]
    has_more = offset + limit < len(selected)
    return SignalTracePage(
        result_fingerprint=result_fingerprint,
        run_fingerprint=trace.run_fingerprint,
        strategy_fingerprint=trace.strategy_fingerprint,
        dataset_fingerprint=trace.dataset_fingerprint,
        signal_trace_fingerprint=signal_trace_fingerprint(trace),
        engine=trace.engine,
        product_id=product_id,
        indicator_ids=trace.indicator_ids,
        total_records=len(records),
        counts=counts,
        outcome=outcome,
        limit=limit,
        offset=offset,
        returned=len(page),
        records=page,
        next_cursor=next_cursor_for(offset + limit) if has_more else None,
    )


def _count(records: tuple[SignalTraceRecord, ...], outcome: EntryConditionOutcome) -> int:
    """Count records with one entry-condition outcome."""
    return sum(1 for item in records if item.entry_condition is outcome)
