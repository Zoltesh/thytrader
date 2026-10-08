"""HTTP response models for immutable backtest results and research submissions.

Summary, list, detail, metrics, benchmark and export responses plus the redacted error
envelope that the ``/api/v1/backtests`` routes declare.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from thytrader.backtest.cost_attribution import BacktestCostAttribution
from thytrader.backtest.models import (
    BacktestBenchmark,
    BacktestDiagnostics,
    BacktestEvaluationWindow,
    BacktestPerformanceMetrics,
    BacktestResult,
    BacktestSummary,
)
from thytrader.backtest.projections import BacktestProjection
from thytrader.evaluation.models import CostAssumptions
from thytrader.research.dataset_binding import BoundDataset


class BacktestSummaryResponse(BaseModel):
    """One newest-first immutable result summary safe for browser discovery.

    ``window`` states which bars the result evaluated (ADR 0094); null when its run
    could not be read.
    """

    model_config = ConfigDict(from_attributes=True)
    result_fingerprint: str
    run_fingerprint: str
    strategy_fingerprint: str
    strategy_id: UUID | None = None
    dataset_fingerprint: str
    published_at: str
    summary: BacktestSummary
    window: BacktestEvaluationWindow | None = None


class BacktestListResponse(BaseModel):
    """Bounded page of immutable backtest result summaries."""

    entries: tuple[BacktestSummaryResponse, ...]
    limit: int
    offset: int
    returned: int
    has_more: bool = False
    next_cursor: str | None = None


class BacktestSubmissionResponse(BaseModel):
    """Evidence identities of one completed run plus the snapshot and datasets it used.

    ``bound_datasets`` echoes every dataset the run bound, including those the server
    chose from the catalog because the request omitted them (ADR 0089).
    """

    run_fingerprint: str
    result_fingerprint: str
    strategy_id: UUID
    strategy_fingerprint: str
    bound_datasets: tuple[BoundDataset, ...] = ()


class BacktestDetailResponse(BaseModel):
    """One fully reverified immutable simulation result plus published run costs.

    ``diagnostics`` (ADR 0090) is the entry funnel recorded beside the result, or null
    for results published before it was recorded. ``window`` (ADR 0094) is the evaluated
    window derived from the source run, outside the fingerprinted result bytes.
    """

    model_config = ConfigDict(from_attributes=True)
    result: BacktestResult
    result_fingerprint: str
    costs: CostAssumptions | None = None
    metrics: BacktestPerformanceMetrics | None = None
    cost_attribution: BacktestCostAttribution | None = None
    diagnostics: BacktestDiagnostics | None = None
    window: BacktestEvaluationWindow | None = None


class BacktestSummaryDetailResponse(BacktestProjection):
    """Bounded backtest projection without trades or equity curves.

    ``window`` names the evaluated bars (evaluation_start/end, warmup_bars, first and
    last evaluated bar), derived from the source run at read time (ADR 0094).
    """


class BacktestMetricsResponse(BaseModel):
    """One derived ratio-metrics report keyed by result fingerprint."""

    metrics: BacktestPerformanceMetrics
    result_fingerprint: str


class BacktestExportResponse(BaseModel):
    """One cursor page of small research projections without full simulation ledgers."""

    entries: tuple[BacktestSummaryDetailResponse, ...]
    returned: int
    has_more: bool
    next_cursor: str | None = None


class BacktestBenchmarkResponse(BaseModel):
    """One deterministic buy-and-hold comparison derived from an immutable result."""

    benchmark: BacktestBenchmark
    result_fingerprint: str


class BacktestErrorDetail(BaseModel):
    """Stable redacted response for backtest-result read failures."""

    code: Literal[
        "backtests_unavailable",
        "backtest_not_found",
        "backtest_invalid",
        "signal_trace_unavailable",
    ]
    message: str


class BacktestErrorResponse(BaseModel):
    """FastAPI-compatible error envelope for backtest-result failures."""

    detail: BacktestErrorDetail
