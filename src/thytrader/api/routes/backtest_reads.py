"""Read-side projections that decorate one immutable backtest result over HTTP.

Bounded summary projections, best-effort diagnostics and derived metrics, and the
source run's published costs and evaluated window (ADR 0090, ADR 0094). None of them
change the result bytes or its identity.
"""

from __future__ import annotations

import logging
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

from thytrader.api.routes.backtest_models import (
    BacktestSummaryDetailResponse,
    BacktestSummaryResponse,
)
from thytrader.backtest.metrics import compute_performance_metrics
from thytrader.backtest.models import (
    BacktestDiagnostics,
    BacktestEvaluationWindow,
    BacktestPerformanceMetrics,
    BacktestResult,
    backtest_evaluation_window,
)
from thytrader.backtest.projections import BacktestProjectionReader
from thytrader.backtest.results import (
    BacktestDiagnosticsReader,
    BacktestResultIntegrityError,
    BacktestResultReader,
    BacktestResultSummaryView,
)
from thytrader.evaluation.models import CostAssumptions, ResearchRunSpecification

_logger = logging.getLogger("thytrader.api.routes.backtests")


async def _bounded_projection(
    store: BacktestResultReader,
    result_fingerprint: str,
    detail: Literal["summary", "full"],
) -> BacktestSummaryDetailResponse | None:
    """Use the bounded capability when available; retain verified legacy-store behavior."""
    if detail != "summary" or not isinstance(store, BacktestProjectionReader):
        return None
    projection = (await store.load_projections((result_fingerprint,)))[0]
    if projection.result_fingerprint != result_fingerprint:
        raise BacktestResultIntegrityError("Projection returned a different result identity.")
    return BacktestSummaryDetailResponse.model_validate(projection.model_dump(mode="python"))


@runtime_checkable
class _BacktestSourceRunLoader(Protocol):
    """Optional store capability used only to project published cost assumptions."""

    async def load_source_specification(self, result: BacktestResult) -> ResearchRunSpecification:
        """Return the verified source run for one loaded result."""
        ...


async def _stored_diagnostics(
    store: BacktestResultReader, result_fingerprint: str
) -> BacktestDiagnostics | None:
    """Best-effort diagnostics: they explain a result and must never hide it."""
    if not isinstance(store, BacktestDiagnosticsReader):
        return None
    try:
        return await store.load_diagnostics(result_fingerprint)
    except Exception as error:  # noqa: BLE001 - diagnostics are advisory evidence only.
        _logger.warning("Backtest diagnostics unavailable: %s", type(error).__name__)
        return None


async def _published_source_projection(
    store: BacktestResultReader,
    result: BacktestResult,
) -> tuple[CostAssumptions | None, BacktestEvaluationWindow | None]:
    """Copy source-run costs and the evaluated window onto the HTTP wrapper.

    Neither is part of the result bytes, so result identity is unchanged (ADR 0094).
    """
    if not isinstance(store, _BacktestSourceRunLoader):
        return None, None
    specification = await store.load_source_specification(result)
    costs = CostAssumptions.model_validate(specification.costs.model_dump(mode="python"))
    try:
        window = backtest_evaluation_window(specification, result.summary.evaluation_bars)
    except ValueError:
        window = None
    return costs, window


def _derived_metrics(result: BacktestResult) -> BacktestPerformanceMetrics | None:
    """Best-effort derived metrics; a failure must not hide the canonical result."""
    try:
        return compute_performance_metrics(result)
    except TypeError, ValueError:
        return None


def _to_summary_response(entry: BacktestResultSummaryView) -> BacktestSummaryResponse:
    """Map one discovery view into its browser-safe response."""
    return BacktestSummaryResponse(
        result_fingerprint=entry.result_fingerprint,
        run_fingerprint=entry.run_fingerprint,
        strategy_fingerprint=entry.strategy_fingerprint,
        strategy_id=None if entry.strategy_id is None else UUID(entry.strategy_id),
        dataset_fingerprint=entry.dataset_fingerprint,
        published_at=entry.published_at.isoformat().replace("+00:00", "Z"),
        summary=entry.summary,
        window=entry.window,
    )
