"""Immutable backtest result and research-submission HTTP presentation.

Read endpoints expose historical evidence; the bounded `POST /api/v1/backtests`
submission endpoint publishes deterministic research results only. No route can
mutate an immutable result or grant paper/live trading authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import TYPE_CHECKING, Annotated, Literal, Protocol, runtime_checkable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, ValidationError

from thytrader.api.dependencies import (
    get_backtest_benchmark_reader,
    get_backtest_result_store,
    get_dataset_store,
    get_research_job_store,
    get_runtime_state,
    get_strategy_snapshot_store,
    get_strategy_store,
)
from thytrader.api.research_binding import dataset_resolver, datasets_missing_http_error
from thytrader.api.research_execution import (
    get_research_execution,
    is_terminal,
    require_research_execution,
    sync_failure,
    wait_for_job,
)
from thytrader.api.strategy_http import snapshot_for_start
from thytrader.backtest.cost_attribution import BacktestCostAttribution, compute_cost_attribution
from thytrader.backtest.metrics import compute_performance_metrics
from thytrader.backtest.models import (
    BacktestBenchmark,
    BacktestDiagnostics,
    BacktestEvaluationWindow,
    BacktestPerformanceMetrics,
    BacktestResult,
    BacktestSummary,
    backtest_benchmark_fingerprint,
    backtest_evaluation_window,
    backtest_result_fingerprint,
)
from thytrader.backtest.projections import BacktestProjection, BacktestProjectionReader
from thytrader.backtest.results import (
    BacktestDiagnosticsReader,
    BacktestResultIntegrityError,
    BacktestResultNotFoundError,
    BacktestResultReader,
    BacktestResultSummaryView,
    BacktestResultUnavailableError,
)
from thytrader.backtest.submission import BacktestStartRequest
from thytrader.evaluation.models import CostAssumptions, ResearchRunSpecification
from thytrader.market_data.datasets import DatasetStore
from thytrader.persistence.backtest_benchmarks import (
    BacktestBenchmarkIntegrityError,
    BacktestBenchmarkNotFoundError,
    BacktestBenchmarkReader,
    BacktestBenchmarkUnavailableError,
)
from thytrader.research.bar_explanations import (
    BAR_EXPLANATION_PAGE_DEFAULT_LIMIT,
    BAR_EXPLANATION_PAGE_MAX_LIMIT,
    BacktestBarExplanationPage,
    bar_explanation_page,
)
from thytrader.research.dataset_binding import (
    BoundDataset,
    DatasetResolver,
    DatasetsMissingError,
    bind_backtest_datasets,
)
from thytrader.research.jobs import (
    ResearchExecutionMode,
    ResearchJobAcceptedResponse,
    ResearchJobRecord,
    ResearchJobStatus,
    ResearchJobStore,
)
from thytrader.research.pagination import decode_offset_cursor, encode_offset_cursor
from thytrader.research.trace_service import (
    SIGNAL_TRACE_PAGE_DEFAULT_LIMIT,
    SIGNAL_TRACE_PAGE_MAX_LIMIT,
    SignalTraceMismatchError,
    SignalTracePage,
    TraceOutcomeFilter,
    evaluate_result_signal_trace,
    signal_trace_page,
)
from thytrader.runtime import RuntimeState
from thytrader.strategies.library import StrategyStore
from thytrader.strategies.snapshots import (
    StrategySnapshotStore,
)

if TYPE_CHECKING:
    from thytrader.evaluation.trace import SignalTrace

router = APIRouter(prefix="/api/v1/backtests", tags=["backtests"])
_logger = logging.getLogger(__name__)

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_BACKTEST_UNAVAILABLE = "Backtest submission is unavailable."
_MAX_LIMIT = 100
_BACKTEST_NOT_FOUND_ERRORS = (BacktestBenchmarkNotFoundError, BacktestResultNotFoundError)


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


def _raise_client_disconnected() -> None:
    """Stop building a large backtest payload after the client disconnects."""
    raise HTTPException(
        status_code=status.HTTP_499_CLIENT_CLOSED_REQUEST,
        detail={"code": "backtest_invalid", "message": "Client disconnected."},
    )


async def _require_connected(http_request: Request) -> None:
    """Avoid fetching further evidence after the requesting client disconnects."""
    if await http_request.is_disconnected():
        _raise_client_disconnected()


def _fingerprint_or_none(value: str | None) -> str | None:
    """Validate one optional fingerprint filter, rejecting malformed identities."""
    if value is None:
        return None
    if _FINGERPRINT_PATTERN.fullmatch(value) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "backtest_invalid", "message": "Fingerprint filter is malformed."},
        )
    return value


def _list_offset(*, offset: int, cursor: str | None) -> int:
    """Prefer an opaque cursor when present; otherwise use the numeric offset."""
    if cursor is None:
        return offset
    try:
        return decode_offset_cursor(cursor)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "backtest_invalid", "message": "Pagination cursor is malformed."},
        ) from None


@router.post(
    "",
    response_model=None,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_201_CREATED: {"model": BacktestSubmissionResponse},
        status.HTTP_202_ACCEPTED: {"model": ResearchJobAcceptedResponse},
    },
)
async def submit_backtest(
    start: BacktestStartRequest,
    job_store: Annotated[ResearchJobStore, Depends(get_research_job_store)],
    strategies: Annotated[StrategyStore, Depends(get_strategy_store)],
    datasets: Annotated[DatasetStore, Depends(get_dataset_store)],
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
    execution: Annotated[ResearchExecutionMode, Depends(get_research_execution)],
    async_submission: Annotated[bool, Query(alias="async")] = False,
) -> BacktestSubmissionResponse | Response:
    """Snapshot the strategy's current rules and queue one historical simulation.

    The strategy must currently validate (422 ``strategy_invalid`` otherwise). Omitted
    datasets bind to the newest complete catalog dataset per clock (422
    ``datasets_missing`` when none is cataloged). The research worker runs the job
    (ADR 0092); a synchronous submit waits up to ``research_sync_wait_seconds`` and
    answers 201 with the result, or 202 with the still-running job. No paper or live
    authority is granted.
    """
    snapshot = await snapshot_for_start(strategies, start.strategy_id)
    resolver = dataset_resolver(datasets, runtime)
    try:
        bound = bind_backtest_datasets(start, snapshot.definition, resolver)
    except DatasetsMissingError as missing:
        raise datasets_missing_http_error(missing) from None
    try:
        request = bound.submission(snapshot.strategy_fingerprint)
    except ValidationError as error:
        raise RequestValidationError(error.errors()) from None
    require_research_execution(execution, detail=_BACKTEST_UNAVAILABLE)
    record = await job_store.create_backtest(request, strategy_id=start.strategy_id)
    waited: float | None = None
    if not async_submission:
        waited = runtime.settings.research_sync_wait_seconds
        finished = await wait_for_job(job_store, record.job_id, timeout_seconds=waited)
        if finished is not None and is_terminal(finished):
            return _completed_backtest(finished, start, snapshot.strategy_fingerprint, resolver)
    body = ResearchJobAcceptedResponse(
        job_id=record.job_id,
        kind=record.kind,
        status=record.status,
        strategy_id=start.strategy_id,
        strategy_fingerprint=snapshot.strategy_fingerprint,
        bound_datasets=resolver.bindings(),
        sync_wait_seconds=waited,
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_202_ACCEPTED,
        media_type="application/json",
    )


def _completed_backtest(
    finished: ResearchJobRecord,
    start: BacktestStartRequest,
    strategy_fingerprint: str,
    resolver: DatasetResolver,
) -> BacktestSubmissionResponse:
    """Answer a finished synchronous backtest exactly as the inline submit did."""
    if (
        finished.status is ResearchJobStatus.COMPLETED
        and finished.run_fingerprint is not None
        and finished.result_fingerprint is not None
    ):
        return BacktestSubmissionResponse(
            run_fingerprint=finished.run_fingerprint,
            result_fingerprint=finished.result_fingerprint,
            strategy_id=start.strategy_id,
            strategy_fingerprint=strategy_fingerprint,
            bound_datasets=resolver.bindings(),
        )
    raise sync_failure(finished, unavailable_detail=_BACKTEST_UNAVAILABLE)


@router.get("/jobs/{job_id}", response_model=ResearchJobRecord)
async def get_backtest_job(
    job_id: UUID,
    job_store: Annotated[ResearchJobStore, Depends(get_research_job_store)],
) -> ResearchJobRecord:
    """Return async backtest job status and fingerprints when complete."""
    record = await job_store.get(job_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "backtest_job_not_found", "message": "Backtest job was not found."},
        )
    return record


@router.get(
    "",
    response_model=BacktestListResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": BacktestErrorResponse}},
)
async def list_backtests(
    store: Annotated[BacktestResultReader, Depends(get_backtest_result_store)],
    run_fingerprint: Annotated[str | None, Query()] = None,
    strategy_fingerprint: Annotated[str | None, Query()] = None,
    dataset_fingerprint: Annotated[str | None, Query()] = None,
    strategy_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    cursor: Annotated[str | None, Query()] = None,
) -> BacktestListResponse:
    """Return a bounded newest-first page of immutable result summaries."""
    selected = [
        _fingerprint_or_none(value)
        for value in (run_fingerprint, strategy_fingerprint, dataset_fingerprint)
        if value is not None
    ]
    if len(selected) + (strategy_id is not None) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "backtest_invalid",
                "message": "Only one source fingerprint filter is accepted per request.",
            },
        )
    start = _list_offset(offset=offset, cursor=cursor)
    try:
        entries = await store.list_summaries(
            run_fingerprint=_fingerprint_or_none(run_fingerprint),
            strategy_fingerprint=_fingerprint_or_none(strategy_fingerprint),
            dataset_fingerprint=_fingerprint_or_none(dataset_fingerprint),
            strategy_id=strategy_id,
            limit=limit + 1,
            offset=start,
        )
    except (BacktestResultUnavailableError, BacktestResultIntegrityError) as error:
        _logger.warning("Backtest list failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "backtests_unavailable",
                "message": "Backtest results are unavailable.",
            },
        ) from None
    except Exception as error:  # noqa: BLE001
        _logger.warning("Backtest list failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "backtests_unavailable",
                "message": "Backtest results are unavailable.",
            },
        ) from None
    has_more = len(entries) > limit
    page = entries[:limit]
    return BacktestListResponse(
        entries=tuple(_to_summary_response(entry) for entry in page),
        limit=limit,
        offset=start,
        returned=len(page),
        has_more=has_more,
        next_cursor=encode_offset_cursor(start + limit) if has_more else None,
    )


@router.get("/export", response_model=BacktestExportResponse)
async def export_backtests(
    store: Annotated[BacktestResultReader, Depends(get_backtest_result_store)],
    strategy_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> BacktestExportResponse:
    """Export publication projections without recalculating historical simulations."""
    if not isinstance(store, BacktestProjectionReader):
        raise HTTPException(
            503,
            detail={"code": "backtests_unavailable", "message": "Bounded export is unavailable."},
        )
    start = _list_offset(offset=0, cursor=cursor)
    try:
        listed = await store.list_summaries(strategy_id=strategy_id, limit=limit + 1, offset=start)
        projections = await store.load_projections(
            tuple(row.result_fingerprint for row in listed[:limit])
        )
    except BacktestResultNotFoundError:
        raise HTTPException(
            404,
            detail={"code": "backtest_not_found", "message": "Backtest publication was not found."},
        ) from None
    except BacktestResultUnavailableError, BacktestResultIntegrityError:
        raise HTTPException(
            503,
            detail={"code": "backtests_unavailable", "message": "Backtest export is unavailable."},
        ) from None
    has_more = len(listed) > limit
    return BacktestExportResponse(
        entries=tuple(
            BacktestSummaryDetailResponse.model_validate(row.model_dump(mode="python"))
            for row in projections
        ),
        returned=len(projections),
        has_more=has_more,
        next_cursor=encode_offset_cursor(start + limit) if has_more else None,
    )


@router.get(
    "/{result_fingerprint}/benchmark",
    response_model=BacktestBenchmarkResponse,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": BacktestErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": BacktestErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": BacktestErrorResponse},
    },
)
async def get_backtest_benchmark(
    reader: Annotated[BacktestBenchmarkReader, Depends(get_backtest_benchmark_reader)],
    result_fingerprint: str,
) -> BacktestBenchmarkResponse:
    """Return a derived comparison while leaving the canonical result immutable."""
    if _FINGERPRINT_PATTERN.fullmatch(result_fingerprint) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "backtest_invalid", "message": "Result fingerprint is malformed."},
        )
    try:
        benchmark = await reader.load(result_fingerprint)
        benchmark = BacktestBenchmark.model_validate(benchmark.model_dump(mode="python"))
        verified_benchmark_fingerprint = backtest_benchmark_fingerprint(benchmark)
    except _BACKTEST_NOT_FOUND_ERRORS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "backtest_not_found", "message": "Backtest result was not found."},
        ) from None
    except (BacktestBenchmarkUnavailableError, BacktestBenchmarkIntegrityError) as error:
        _logger.warning("Backtest benchmark failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "backtests_unavailable",
                "message": "Backtest benchmark is unavailable.",
            },
        ) from None
    except Exception as error:  # noqa: BLE001
        _logger.warning("Backtest benchmark failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "backtests_unavailable",
                "message": "Backtest benchmark is unavailable.",
            },
        ) from None
    if benchmark.result_fingerprint != result_fingerprint:
        _logger.warning("Backtest benchmark returned mismatched result identity")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "backtests_unavailable",
                "message": "Backtest benchmark is unavailable.",
            },
        )
    if verified_benchmark_fingerprint != benchmark.benchmark_fingerprint:
        _logger.warning("Backtest benchmark returned mismatched derived identity")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "backtests_unavailable",
                "message": "Backtest benchmark is unavailable.",
            },
        )
    return BacktestBenchmarkResponse(
        benchmark=benchmark,
        result_fingerprint=result_fingerprint,
    )


@router.get(
    "/{result_fingerprint}/metrics",
    response_model=BacktestMetricsResponse,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": BacktestErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": BacktestErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": BacktestErrorResponse},
    },
)
async def get_backtest_metrics(
    store: Annotated[BacktestResultReader, Depends(get_backtest_result_store)],
    result_fingerprint: str,
) -> BacktestMetricsResponse:
    """Return derived ratio metrics while leaving the canonical result immutable."""
    if _FINGERPRINT_PATTERN.fullmatch(result_fingerprint) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "backtest_invalid", "message": "Result fingerprint is malformed."},
        )
    try:
        result = await store.load(result_fingerprint)
        verified = backtest_result_fingerprint(result)
        metrics = compute_performance_metrics(result)
    except BacktestResultNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "backtest_not_found", "message": "Backtest result was not found."},
        ) from None
    except (BacktestResultUnavailableError, BacktestResultIntegrityError) as error:
        _logger.warning("Backtest metrics failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "backtests_unavailable",
                "message": "Backtest metrics are unavailable.",
            },
        ) from None
    except Exception as error:  # noqa: BLE001
        _logger.warning("Backtest metrics failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "backtests_unavailable",
                "message": "Backtest metrics are unavailable.",
            },
        ) from None
    if verified != result_fingerprint or metrics.result_fingerprint != result_fingerprint:
        _logger.warning("Backtest metrics returned mismatched result identity")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "backtests_unavailable",
                "message": "Backtest metrics are unavailable.",
            },
        )
    return BacktestMetricsResponse(metrics=metrics, result_fingerprint=result_fingerprint)


@router.get(
    "/{result_fingerprint}",
    response_model=BacktestSummaryDetailResponse | BacktestDetailResponse,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": BacktestErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": BacktestErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": BacktestErrorResponse},
    },
)
async def get_backtest(
    http_request: Request,
    store: Annotated[BacktestResultReader, Depends(get_backtest_result_store)],
    result_fingerprint: str,
    detail: Annotated[Literal["summary", "full"], Query()] = "summary",
) -> BacktestSummaryDetailResponse | BacktestDetailResponse:
    """Load one immutable result as a bounded summary (default) or full document."""
    if _FINGERPRINT_PATTERN.fullmatch(result_fingerprint) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "backtest_invalid", "message": "Result fingerprint is malformed."},
        )
    try:
        await _require_connected(http_request)
        projection = await _bounded_projection(store, result_fingerprint, detail)
        if projection is not None:
            return projection
        result = await store.load(result_fingerprint)
        await _require_connected(http_request)
        verified_result_fingerprint = backtest_result_fingerprint(result)
        costs, window = await _published_source_projection(store, result)
    except BacktestResultNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "backtest_not_found", "message": "Backtest result was not found."},
        ) from None
    except (BacktestResultUnavailableError, BacktestResultIntegrityError) as error:
        _logger.warning("Backtest detail failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "backtests_unavailable",
                "message": "Backtest results are unavailable.",
            },
        ) from None
    except HTTPException:
        raise
    except Exception as error:  # noqa: BLE001
        _logger.warning("Backtest detail failed: %s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "backtests_unavailable",
                "message": "Backtest results are unavailable.",
            },
        ) from None
    if verified_result_fingerprint != result_fingerprint:
        _logger.warning("Backtest detail returned mismatched result identity")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "backtests_unavailable",
                "message": "Backtest results are unavailable.",
            },
        )
    metrics = _derived_metrics(result)
    attribution = compute_cost_attribution(result)
    diagnostics = await _stored_diagnostics(store, result_fingerprint)
    if detail == "summary":
        return BacktestSummaryDetailResponse(
            result_fingerprint=result_fingerprint,
            run_fingerprint=result.run_fingerprint,
            strategy_fingerprint=result.strategy_fingerprint,
            dataset_fingerprint=result.dataset_fingerprint,
            summary=result.summary,
            costs=costs,
            metrics=metrics,
            cost_attribution=attribution,
            diagnostics=diagnostics,
            window=window,
            verification_scope="full_artifacts",
        )
    return BacktestDetailResponse(
        result=result,
        result_fingerprint=result_fingerprint,
        costs=costs,
        metrics=metrics,
        cost_attribution=attribution,
        diagnostics=diagnostics,
        window=window,
    )


@router.get(
    "/{result_fingerprint}/signal-trace",
    response_model=SignalTracePage,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": BacktestErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": BacktestErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": BacktestErrorResponse},
    },
)
async def get_backtest_signal_trace(
    store: Annotated[BacktestResultReader, Depends(get_backtest_result_store)],
    snapshots: Annotated[StrategySnapshotStore, Depends(get_strategy_snapshot_store)],
    datasets: Annotated[DatasetStore, Depends(get_dataset_store)],
    result_fingerprint: str,
    outcome: Annotated[TraceOutcomeFilter, Query()] = "all",
    limit: Annotated[
        int, Query(ge=1, le=SIGNAL_TRACE_PAGE_MAX_LIMIT)
    ] = SIGNAL_TRACE_PAGE_DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> SignalTracePage:
    """Re-evaluate the entry-condition trace of one result's run, one bounded page at a time.

    Read-only (ADR 0090): the API evaluates the exact published run against its verified
    datasets and fails closed unless the trace reproduces the result's
    ``signal_trace_fingerprint``. Multi-instrument documents trace the primary product.
    """
    if _FINGERPRINT_PATTERN.fullmatch(result_fingerprint) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "backtest_invalid", "message": "Result fingerprint is malformed."},
        )
    offset = _list_offset(offset=0, cursor=cursor)
    if not isinstance(store, _BacktestSourceRunLoader):
        raise _trace_unavailable("Published research runs are unavailable.")
    try:
        result = await store.load(result_fingerprint)
        specification = await store.load_source_specification(result)
        evaluated = await evaluate_result_signal_trace(
            result,
            specification=specification,
            strategy_store=snapshots,
            dataset_store=datasets,
        )
    except BacktestResultNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "backtest_not_found", "message": "Backtest result was not found."},
        ) from None
    except SignalTraceMismatchError as error:
        raise _trace_unavailable(str(error)) from None
    except Exception as error:  # noqa: BLE001 - redacted boundary for store/dataset faults.
        _logger.warning("Backtest signal trace failed: %s", type(error).__name__)
        raise _trace_unavailable(
            "The run's strategy snapshot or verified datasets could not be loaded."
        ) from None
    return signal_trace_page(
        evaluated.trace,
        result_fingerprint=result_fingerprint,
        product_id=evaluated.product_id,
        outcome=outcome,
        limit=limit,
        offset=offset,
        next_cursor_for=encode_offset_cursor,
    )


def _trace_unavailable(message: str) -> HTTPException:
    """Build the 503 envelope for a trace that could not be re-evaluated or verified."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"code": "signal_trace_unavailable", "message": message},
    )


@router.get(
    "/{result_fingerprint}/bar-explanations",
    response_model=BacktestBarExplanationPage,
    responses={
        status.HTTP_400_BAD_REQUEST: {"model": BacktestErrorResponse},
        status.HTTP_404_NOT_FOUND: {"model": BacktestErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": BacktestErrorResponse},
    },
)
async def get_backtest_bar_explanations(
    store: Annotated[BacktestResultReader, Depends(get_backtest_result_store)],
    snapshots: Annotated[StrategySnapshotStore, Depends(get_strategy_snapshot_store)],
    datasets: Annotated[DatasetStore, Depends(get_dataset_store)],
    result_fingerprint: str,
    limit: Annotated[
        int, Query(ge=1, le=BAR_EXPLANATION_PAGE_MAX_LIMIT)
    ] = BAR_EXPLANATION_PAGE_DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query()] = None,
) -> BacktestBarExplanationPage:
    """Explain one result bar by bar: signals read, fills simulated, equity marked.

    Read-only (ADR 0116): reuses the fingerprint-verified signal-trace re-evaluation
    and joins the immutable result's own trades and equity curve, so nothing is
    re-simulated, interpolated, or lookahead-contaminated. One bounded page of bars,
    oldest first, with result/run/strategy/dataset provenance.
    """
    if _FINGERPRINT_PATTERN.fullmatch(result_fingerprint) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "backtest_invalid", "message": "Result fingerprint is malformed."},
        )
    offset = _list_offset(offset=0, cursor=cursor)
    if not isinstance(store, _BacktestSourceRunLoader):
        raise _explanations_unavailable("Published research runs are unavailable.")
    loaded = await _load_explanation_inputs(store, snapshots, datasets, result_fingerprint)
    return bar_explanation_page(
        loaded.trace,
        loaded.result,
        window=loaded.window,
        product_id=loaded.product_id,
        limit=limit,
        offset=offset,
        result_fingerprint=result_fingerprint,
        next_cursor_for=encode_offset_cursor,
    )


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
