"""Immutable backtest result and research-submission HTTP presentation.

Read endpoints expose historical evidence; the bounded `POST /api/v1/backtests`
submission endpoint publishes deterministic research results only. No route can
mutate an immutable result or grant paper/live trading authority.
"""

from __future__ import annotations

import logging
import re
from typing import Annotated, Literal, Protocol, runtime_checkable
from uuid import UUID  # noqa: TC003 - FastAPI path parameter binding

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict

from thytrader.api.dependencies import (
    get_backtest_benchmark_reader,
    get_backtest_result_store,
    get_backtest_submitter,
    get_research_job_store,
)
from thytrader.backtest.metrics import compute_performance_metrics
from thytrader.backtest.models import (
    BacktestBenchmark,
    BacktestPerformanceMetrics,
    BacktestResult,
    BacktestSummary,
    backtest_benchmark_fingerprint,
    backtest_result_fingerprint,
)
from thytrader.backtest.submission import (
    BacktestSubmissionError,
    BacktestSubmissionRejectedError,
    BacktestSubmissionRequest,
    BacktestSubmitter,
)
from thytrader.persistence.backtest_benchmarks import (
    BacktestBenchmarkIntegrityError,
    BacktestBenchmarkNotFoundError,
    BacktestBenchmarkReader,
    BacktestBenchmarkUnavailableError,
)
from thytrader.persistence.backtest_results import (
    BacktestResultIntegrityError,
    BacktestResultNotFoundError,
    BacktestResultReader,
    BacktestResultSummaryView,
    BacktestResultUnavailableError,
)
from thytrader.research.jobs import (
    ResearchJobAcceptedResponse,
    ResearchJobRecord,
    ResearchJobStore,
)
from thytrader.research.models import CostAssumptions, ResearchRunSpecification
from thytrader.research.pagination import decode_offset_cursor, encode_offset_cursor

router = APIRouter(prefix="/api/v1/backtests", tags=["backtests"])
_logger = logging.getLogger(__name__)

_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_LIMIT = 100
_BACKTEST_NOT_FOUND_ERRORS = (BacktestBenchmarkNotFoundError, BacktestResultNotFoundError)


class BacktestSummaryResponse(BaseModel):
    """One newest-first immutable result summary safe for browser discovery."""

    model_config = ConfigDict(from_attributes=True)
    result_fingerprint: str
    run_fingerprint: str
    strategy_fingerprint: str
    dataset_fingerprint: str
    engine_contract_version: str
    published_at: str
    summary: BacktestSummary


class BacktestListResponse(BaseModel):
    """Bounded page of immutable backtest result summaries."""

    entries: tuple[BacktestSummaryResponse, ...]
    limit: int
    offset: int
    returned: int
    has_more: bool = False
    next_cursor: str | None = None


class BacktestSubmissionResponse(BaseModel):
    """Immutable evidence identities emitted by one completed research submission."""

    run_fingerprint: str
    result_fingerprint: str


class BacktestDetailResponse(BaseModel):
    """One fully reverified immutable simulation result plus published run costs."""

    model_config = ConfigDict(from_attributes=True)
    result: BacktestResult
    result_fingerprint: str
    costs: CostAssumptions | None = None
    metrics: BacktestPerformanceMetrics | None = None


class BacktestSummaryDetailResponse(BaseModel):
    """Bounded backtest projection without trades or equity curves."""

    model_config = ConfigDict(from_attributes=True)
    result_fingerprint: str
    run_fingerprint: str
    strategy_fingerprint: str
    dataset_fingerprint: str
    engine_contract_version: str
    summary: BacktestSummary
    costs: CostAssumptions | None = None
    metrics: BacktestPerformanceMetrics | None = None


class BacktestMetricsResponse(BaseModel):
    """One derived ratio-metrics report keyed by result fingerprint."""

    metrics: BacktestPerformanceMetrics
    result_fingerprint: str


class BacktestBenchmarkResponse(BaseModel):
    """One deterministic buy-and-hold comparison derived from an immutable result."""

    benchmark: BacktestBenchmark
    result_fingerprint: str


class BacktestErrorDetail(BaseModel):
    """Stable redacted response for backtest-result read failures."""

    code: Literal["backtests_unavailable", "backtest_not_found", "backtest_invalid"]
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


async def _published_costs_projection(
    store: BacktestResultReader,
    result: BacktestResult,
) -> CostAssumptions | None:
    """Copy source-run CostAssumptions onto the HTTP wrapper without altering result identity."""
    if not isinstance(store, _BacktestSourceRunLoader):
        return None
    specification = await store.load_source_specification(result)
    return CostAssumptions.model_validate(specification.costs.model_dump(mode="python"))


def _raise_client_disconnected() -> None:
    """Stop building a large backtest payload after the client disconnects."""
    raise HTTPException(
        status_code=status.HTTP_499_CLIENT_CLOSED_REQUEST,
        detail={"code": "backtest_invalid", "message": "Client disconnected."},
    )


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
    request: BacktestSubmissionRequest,
    submitter: Annotated[BacktestSubmitter, Depends(get_backtest_submitter)],
    job_store: Annotated[ResearchJobStore, Depends(get_research_job_store)],
    async_submission: Annotated[bool, Query(alias="async")] = False,
) -> BacktestSubmissionResponse | Response:
    """Submit one immutable historical simulation without paper or live authority."""
    if async_submission:
        record = await job_store.create_backtest(request)
        body = ResearchJobAcceptedResponse(
            job_id=record.job_id,
            kind=record.kind,
            status=record.status,
        )
        return Response(
            content=body.model_dump_json(),
            status_code=status.HTTP_202_ACCEPTED,
            media_type="application/json",
        )
    try:
        result = await submitter.submit(request)
    except BacktestSubmissionRejectedError as rejected:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "backtest_window_rejected",
                "message": str(rejected),
            },
        ) from None
    except BacktestSubmissionError as error:
        _logger.warning("backtest_submission_failed error_class=%s", type(error.__cause__).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Backtest submission is unavailable.",
        ) from None
    return BacktestSubmissionResponse(
        run_fingerprint=result.run_fingerprint,
        result_fingerprint=result.result_fingerprint,
    )


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
    if len(selected) > 1:
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
        if await http_request.is_disconnected():
            _raise_client_disconnected()
        result = await store.load(result_fingerprint)
        if await http_request.is_disconnected():
            _raise_client_disconnected()
        verified_result_fingerprint = backtest_result_fingerprint(result)
        costs = await _published_costs_projection(store, result)
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
    if detail == "summary":
        return BacktestSummaryDetailResponse(
            result_fingerprint=result_fingerprint,
            run_fingerprint=result.run_fingerprint,
            strategy_fingerprint=result.strategy_fingerprint,
            dataset_fingerprint=result.dataset_fingerprint,
            engine_contract_version=result.engine_contract_version,
            summary=result.summary,
            costs=costs,
            metrics=metrics,
        )
    return BacktestDetailResponse(
        result=result,
        result_fingerprint=result_fingerprint,
        costs=costs,
        metrics=metrics,
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
        dataset_fingerprint=entry.dataset_fingerprint,
        engine_contract_version=entry.engine_contract_version,
        published_at=entry.published_at.isoformat().replace("+00:00", "Z"),
        summary=entry.summary,
    )
