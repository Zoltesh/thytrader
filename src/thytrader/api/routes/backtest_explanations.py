"""Verified inputs for the per-bar backtest explanation route (ADR 0116).

Loads one immutable result and its source run, re-evaluates the fingerprint-verified
signal trace, and maps store faults to redacted HTTP errors.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from fastapi import HTTPException, status

from thytrader.backtest.models import (
    BacktestEvaluationWindow,
    BacktestResult,
    backtest_evaluation_window,
    backtest_result_fingerprint,
)
from thytrader.backtest.results import (
    BacktestResultIntegrityError,
    BacktestResultNotFoundError,
    BacktestResultReader,
    BacktestResultUnavailableError,
)
from thytrader.research.trace_service import (
    SignalTraceMismatchError,
    evaluate_result_signal_trace,
)

if TYPE_CHECKING:
    from thytrader.evaluation.models import ResearchRunSpecification
    from thytrader.evaluation.trace import SignalTrace
    from thytrader.market_data.datasets import DatasetStore
    from thytrader.strategies.snapshots import StrategySnapshotStore

_logger = logging.getLogger("thytrader.api.routes.backtests")


@dataclass(frozen=True, slots=True)
class _ExplanationInputs:
    """Verified trace, immutable result, and derived window for one explanation page."""

    trace: SignalTrace
    result: BacktestResult
    window: BacktestEvaluationWindow
    product_id: str


async def _load_explanation_inputs(
    store: BacktestResultReader,
    snapshots: StrategySnapshotStore,
    datasets: DatasetStore,
    result_fingerprint: str,
) -> _ExplanationInputs:
    """Load and verify one result's trace; map store faults to redacted HTTP errors."""
    try:
        return await _verified_explanation_inputs(
            _explanation_store(store), snapshots, datasets, result_fingerprint
        )
    except BacktestResultNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "backtest_not_found", "message": "Backtest result was not found."},
        ) from None
    except SignalTraceMismatchError as error:
        raise _explanations_unavailable(str(error)) from None
    except Exception as error:  # noqa: BLE001 - redacted boundary for store/dataset faults.
        _logger.warning("Backtest bar explanations failed: %s", type(error).__name__)
        raise _explanations_unavailable(
            "The run's strategy snapshot or verified datasets could not be loaded."
        ) from None


@runtime_checkable
class _ExplanationStore(Protocol):
    """A result reader that can also load the verified source run."""

    async def load(self, result_fingerprint: str) -> BacktestResult:
        """Load one immutable result."""
        ...

    async def load_source_specification(self, result: BacktestResult) -> ResearchRunSpecification:
        """Return the verified source run for one loaded result."""
        ...


def _explanation_store(store: BacktestResultReader) -> _ExplanationStore:
    """Narrow a result reader that can also load its source run."""
    if isinstance(store, _ExplanationStore):
        return store
    raise BacktestResultUnavailableError("Published research runs are unavailable.")


async def _verified_explanation_inputs(
    store: _ExplanationStore,
    snapshots: StrategySnapshotStore,
    datasets: DatasetStore,
    result_fingerprint: str,
) -> _ExplanationInputs:
    """Re-evaluate the trace and reject a result whose identity does not match."""
    result = await store.load(result_fingerprint)
    _require_result_identity(result, result_fingerprint)
    specification = await store.load_source_specification(result)
    evaluated = await evaluate_result_signal_trace(
        result,
        specification=specification,
        strategy_store=snapshots,
        dataset_store=datasets,
    )
    return _ExplanationInputs(
        trace=evaluated.trace,
        result=result,
        window=backtest_evaluation_window(specification, result.summary.evaluation_bars),
        product_id=evaluated.product_id,
    )


def _require_result_identity(result: BacktestResult, result_fingerprint: str) -> None:
    """Refuse a store that returns a different result than the one requested."""
    if backtest_result_fingerprint(result) != result_fingerprint:
        _logger.warning("Backtest bar explanations returned mismatched result identity")
        raise BacktestResultIntegrityError("Backtest result identity does not match.")


def _explanations_unavailable(message: str) -> HTTPException:
    """Build the 503 envelope for bar explanations that could not be verified."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "bar_explanations_unavailable", "message": message},
    )
