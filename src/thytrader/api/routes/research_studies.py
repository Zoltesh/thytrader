"""Research-study, template, and backtest-model HTTP presentation.

These routes compose unified-model backtests. They cannot mutate published
results or grant paper/live trading authority.
"""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, ValidationError

from thytrader.api.dependencies import (
    get_backtest_result_store,
    get_backtest_submitter,
    get_dataset_store,
    get_execution_store,
    get_research_job_store,
    get_research_study_catalog,
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
from thytrader.api.strategy_http import strategy_http_error
from thytrader.backtest.results import BacktestResultReader
from thytrader.backtest.submission import BacktestSubmitter  # noqa: TC001 - FastAPI Depends.
from thytrader.market_data.datasets import DatasetStore
from thytrader.persistence.postgres_research_jobs import ResearchJobUnavailableError
from thytrader.research.backtest_model import BacktestModelDescription, backtest_model_description
from thytrader.research.catalog import (
    ResearchStudyCatalog,
    StudyCatalogIntegrityError,
    StudyCatalogNotFoundError,
    StudyCatalogSummary,
    StudyCatalogUnavailableError,
)
from thytrader.research.dataset_binding import (
    BoundDataset,
    DatasetsMissingError,
)
from thytrader.research.jobs import (
    ResearchExecutionMode,
    ResearchJobAcceptedResponse,
    ResearchJobListResponse,
    ResearchJobRecord,
    ResearchJobStatus,
    ResearchJobStore,
)
from thytrader.research.promotion import PromotionEvidence, assemble_promotion_evidence
from thytrader.research.studies import (
    SYNC_STUDY_BUDGET,
    ResearchStudy,
    ResearchStudyError,
    ResearchStudyPlan,
    ResearchStudyPlanSummary,
    ResearchStudyService,
    ResearchStudySummary,
    StudyKind,
    load_candidate_definitions,
)
from thytrader.research.study_planning import StudyBudgetError, StudyPlanningError
from thytrader.research.study_start import (
    BoundStudyStart,
    ResearchStudyStartRequest,
    bind_study_start,
)
from thytrader.research.study_summaries import (
    summarize_research_study,
    summarize_research_study_plan,
)
from thytrader.runtime import RuntimeState
from thytrader.strategies.library import (
    StrategyLibraryError,
    StrategyStore,
)
from thytrader.strategies.snapshots import (
    StrategySnapshotError,
    StrategySnapshotStore,
)
from thytrader.strategies.template_blueprints import template_blueprint
from thytrader.strategies.template_ids import parse_template_id, template_catalog
from thytrader.trading.models import ExecutionStoreError
from thytrader.trading.store import ExecutionStore

router = APIRouter(prefix="/api/v1/research", tags=["research"])
_logger = logging.getLogger(__name__)
_STUDY_UNAVAILABLE = "Research study submission is unavailable."


class StrategyTemplateEntry(BaseModel):
    """One fail-closed draft template identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str
    description: str


class StrategyTemplateDetail(BaseModel):
    """One template's defaults, indicator ids, and sweepable axes."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    warmup_bars: int
    indicator_ids: tuple[str, ...]
    defaults: dict[str, str]
    sweepable_axes: tuple[dict[str, object], ...]


class StrategyTemplateListResponse(BaseModel):
    """Catalog of research draft templates."""

    templates: tuple[StrategyTemplateEntry, ...]


class StrategyTemplateDetailResponse(BaseModel):
    """One template blueprint keyed by id."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    template: StrategyTemplateDetail


class StudyCatalogListResponse(BaseModel):
    """Newest-first persisted study catalog rows."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    studies: tuple[StudyCatalogSummary, ...]


class StudyBindingEcho(BaseModel):
    """What a study start bound: every dataset and the exact evaluation window (ADR 0089).

    These fields echo the request; they are not part of the canonical plan or study
    document and do not change any fingerprint.
    """

    bound_datasets: tuple[BoundDataset, ...]
    evaluation_start: datetime
    evaluation_end: datetime


class StudyPlanSummaryResponse(ResearchStudyPlanSummary, StudyBindingEcho):
    """Compact plan summary plus the datasets and window it was planned on."""


class StudyPlanDetailResponse(ResearchStudyPlan, StudyBindingEcho):
    """Full window schedule plus the datasets and window it was planned on."""


class ResearchStudySubmitResponse(ResearchStudy, StudyBindingEcho):
    """The derived study document plus the datasets and window it ran on."""


def _echo(bound: BoundStudyStart) -> dict[str, object]:
    """Return the binding echo fields for one bound study start."""
    return {
        "bound_datasets": bound.bound_datasets,
        "evaluation_start": bound.request.evaluation_start,
        "evaluation_end": bound.request.evaluation_end,
    }


def _planning_http_error(error: StudyPlanningError) -> HTTPException:
    """Map a planning rejection to 422, naming budget overruns separately."""
    budget = isinstance(error, StudyBudgetError)
    code = "study_budget_exceeded" if budget else "study_window_rejected"
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": code, "message": str(error)},
    )


def _study_service(
    publications: Annotated[StrategySnapshotStore, Depends(get_strategy_snapshot_store)],
    submitter: Annotated[BacktestSubmitter, Depends(get_backtest_submitter)],
    results: Annotated[BacktestResultReader, Depends(get_backtest_result_store)],
    catalog: Annotated[ResearchStudyCatalog, Depends(get_research_study_catalog)],
    datasets: Annotated[DatasetStore, Depends(get_dataset_store)],
) -> ResearchStudyService:
    """Compose studies from the same publication and backtest services as single runs."""
    return ResearchStudyService(
        publications=publications,
        submitter=submitter,
        results=results,
        catalog=catalog,
        datasets=datasets,
    )


@router.get("/backtest-model", response_model=BacktestModelDescription)
def get_backtest_model() -> BacktestModelDescription:
    """Describe the single unified backtest model's assumptions without trading authority."""
    return backtest_model_description()


@router.get("/templates", response_model=StrategyTemplateListResponse)
def get_strategy_templates() -> StrategyTemplateListResponse:
    """List fail-closed draft templates agents and the UI can create."""
    entries = tuple(
        StrategyTemplateEntry(id=item["id"], name=item["name"], description=item["description"])
        for item in template_catalog()
    )
    return StrategyTemplateListResponse(templates=entries)


@router.get("/templates/{template_id}", response_model=StrategyTemplateDetailResponse)
def get_strategy_template_detail(template_id: str) -> StrategyTemplateDetailResponse:
    """Return one template's defaults and sweepable parameter axes."""
    try:
        parsed = parse_template_id(template_id)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "template_not_found", "message": str(error)},
        ) from None
    blueprint = template_blueprint(parsed)
    detail = StrategyTemplateDetail(
        id=str(blueprint["id"]),
        warmup_bars=int(blueprint["warmup_bars"]),
        indicator_ids=tuple(str(item) for item in blueprint["indicator_ids"]),
        defaults={str(key): str(value) for key, value in blueprint["defaults"].items()},
        sweepable_axes=tuple(dict(axis) for axis in blueprint["sweepable_axes"]),
    )
    return StrategyTemplateDetailResponse(template=detail)


@router.post(
    "/studies/plan",
    response_model=StudyPlanSummaryResponse | StudyPlanDetailResponse,
)
async def plan_research_study(
    start: ResearchStudyStartRequest,
    service: Annotated[ResearchStudyService, Depends(_study_service)],
    strategies: Annotated[StrategyStore, Depends(get_strategy_store)],
    publications: Annotated[StrategySnapshotStore, Depends(get_strategy_snapshot_store)],
    datasets: Annotated[DatasetStore, Depends(get_dataset_store)],
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
    detail: Annotated[Literal["summary", "full"], Query()] = "summary",
) -> StudyPlanSummaryResponse | StudyPlanDetailResponse:
    """Return a compact plan summary (default) or the full window schedule.

    Planning snapshots each named strategy's current rules and binds omitted datasets
    and bounds exactly as submit does. It plans with the async budget; warnings say
    when a study is too large for a synchronous submit.
    """
    bound = await _bound_study(start, strategies, publications, datasets, runtime)
    try:
        plan = await service.plan(bound.request)
    except StudyPlanningError as error:
        raise _planning_http_error(error) from None
    except (ResearchStudyError, StrategySnapshotError) as error:
        _logger.warning("research_study_plan_failed error_class=%s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Research study planning is unavailable.",
        ) from None
    if detail == "full":
        return StudyPlanDetailResponse.model_validate({**dict(plan), **_echo(bound)})
    summary = summarize_research_study_plan(plan)
    return StudyPlanSummaryResponse.model_validate({**dict(summary), **_echo(bound)})


@router.post(
    "/studies",
    response_model=None,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_201_CREATED: {"model": ResearchStudy},
        status.HTTP_202_ACCEPTED: {"model": ResearchJobAcceptedResponse},
    },
)
async def submit_research_study(
    start: ResearchStudyStartRequest,
    service: Annotated[ResearchStudyService, Depends(_study_service)],
    job_store: Annotated[ResearchJobStore, Depends(get_research_job_store)],
    strategies: Annotated[StrategyStore, Depends(get_strategy_store)],
    publications: Annotated[StrategySnapshotStore, Depends(get_strategy_snapshot_store)],
    datasets: Annotated[DatasetStore, Depends(get_dataset_store)],
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
    catalog: Annotated[ResearchStudyCatalog, Depends(get_research_study_catalog)],
    execution: Annotated[ResearchExecutionMode, Depends(get_research_execution)],
    async_submission: Annotated[bool, Query(alias="async")] = False,
) -> ResearchStudySubmitResponse | Response:
    """Pin strategy and dataset bindings, then queue an asynchronously planned study.

    A synchronous submit allows at most 8 candidates and 128 child windows; an async
    job (``?async=true``) may use the larger async budget. Async planning failures are
    recorded on the durable job; synchronous submissions retain their 422 preflight. The research
    worker runs the children (ADR 0092); a synchronous submit waits up to
    ``research_sync_wait_seconds`` and answers 201 with the study, or 202 with the
    still-running job.
    """
    bound = await _bound_study(start, strategies, publications, datasets, runtime)
    request = bound.request
    if not async_submission:
        try:
            await service.plan(request, budget=SYNC_STUDY_BUDGET)
        except StudyPlanningError as error:
            raise _planning_http_error(error) from None
        except (ResearchStudyError, StrategySnapshotError) as error:
            _logger.warning("research_study_queue_failed error_class=%s", type(error).__name__)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=_STUDY_UNAVAILABLE,
            ) from None
    require_research_execution(execution, detail=_STUDY_UNAVAILABLE)
    primary = start.primary_strategy_id()
    record = await job_store.create_study(request, strategy_id=primary)
    waited: float | None = None
    if not async_submission:
        waited = runtime.settings.research_sync_wait_seconds
        finished = await wait_for_job(job_store, record.job_id, timeout_seconds=waited)
        if finished is not None and is_terminal(finished):
            return await _completed_study(finished, catalog, bound)
    body = ResearchJobAcceptedResponse(
        job_id=record.job_id,
        kind=record.kind,
        status=record.status,
        strategy_id=primary,
        strategy_fingerprint=record.strategy_fingerprint,
        bound_datasets=bound.bound_datasets,
        evaluation_start=request.evaluation_start,
        evaluation_end=request.evaluation_end,
        sync_wait_seconds=waited,
    )
    return Response(
        content=body.model_dump_json(),
        status_code=status.HTTP_202_ACCEPTED,
        media_type="application/json",
    )


async def _completed_study(
    finished: ResearchJobRecord,
    catalog: ResearchStudyCatalog,
    bound: BoundStudyStart,
) -> ResearchStudySubmitResponse:
    """Answer a finished synchronous study exactly as the inline submit did.

    The worker persisted the study in the catalog; the response is that canonical
    document plus the binding echo (the same reload the plan-dedupe path returns).
    """
    if finished.status is not ResearchJobStatus.COMPLETED or finished.study_fingerprint is None:
        raise sync_failure(finished, unavailable_detail=_STUDY_UNAVAILABLE)
    try:
        study = ResearchStudy.model_validate_json(await catalog.load(finished.study_fingerprint))
    except (
        StudyCatalogNotFoundError,
        StudyCatalogUnavailableError,
        StudyCatalogIntegrityError,
        ValidationError,
        ValueError,
    ) as error:
        _logger.warning("research_study_reload_failed error_class=%s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_STUDY_UNAVAILABLE,
        ) from None
    return ResearchStudySubmitResponse.model_validate({**dict(study), **_echo(bound)})


@router.get("/jobs", response_model=ResearchJobListResponse)
async def list_research_jobs(
    strategy_id: Annotated[UUID, Query()],
    job_store: Annotated[ResearchJobStore, Depends(get_research_job_store)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ResearchJobListResponse:
    """Return one strategy's newest async backtest and study jobs."""
    try:
        jobs = await job_store.list_for_strategy(strategy_id, limit=limit)
    except ResearchJobUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "research_jobs_unavailable",
                "message": "Research jobs are unavailable.",
            },
        ) from None
    return ResearchJobListResponse(jobs=jobs, limit=limit, returned=len(jobs))


async def _bound_study(
    start: ResearchStudyStartRequest,
    strategies: StrategyStore,
    publications: StrategySnapshotStore,
    datasets: DatasetStore,
    runtime: RuntimeState,
) -> BoundStudyStart:
    """Snapshot strategies, derive market variants, and bind datasets and bounds."""
    try:
        return await bind_study_start(
            start,
            strategies=strategies,
            publications=publications,
            resolver=dataset_resolver(datasets, runtime),
            datasets=datasets,
        )
    except StudyPlanningError as error:
        raise _planning_http_error(error) from None
    except DatasetsMissingError as missing:
        raise datasets_missing_http_error(missing) from None
    except StrategyLibraryError as error:
        raise strategy_http_error(error) from None
    except StrategySnapshotError as error:
        _logger.warning("research_study_bind_failed error_class=%s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Research study planning is unavailable.",
        ) from None
    except ValidationError as error:
        raise RequestValidationError(error.errors()) from None


@router.get("/jobs/{job_id}", response_model=ResearchJobRecord)
async def get_research_job(
    job_id: UUID,
    job_store: Annotated[ResearchJobStore, Depends(get_research_job_store)],
) -> ResearchJobRecord:
    """Return async research job status and fingerprints when complete."""
    record = await job_store.get(job_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "research_job_not_found", "message": "Research job was not found."},
        )
    return record


@router.post("/jobs/{job_id}/cancel", response_model=ResearchJobRecord)
async def cancel_research_job(
    job_id: UUID,
    job_store: Annotated[ResearchJobStore, Depends(get_research_job_store)],
) -> ResearchJobRecord:
    """Cancel one queued or running research job."""
    try:
        return await job_store.cancel(job_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "research_job_not_found", "message": "Research job was not found."},
        ) from None
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "research_job_rejected", "message": str(error)},
        ) from None


@router.get("/studies", response_model=StudyCatalogListResponse)
async def list_research_studies(
    catalog: Annotated[ResearchStudyCatalog, Depends(get_research_study_catalog)],
    kind: StudyKind | None = None,
    strategy_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> StudyCatalogListResponse:
    """List persisted studies (optionally every study that includes one strategy)."""
    try:
        rows = await catalog.list_summaries(
            kind=kind.value if kind is not None else None,
            strategy_id=strategy_id,
            limit=limit,
            offset=offset,
        )
    except StudyCatalogIntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "study_catalog_rejected", "message": str(error)},
        ) from None
    except StudyCatalogUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Research study catalog is unavailable.",
        ) from None
    return StudyCatalogListResponse(studies=rows)


@router.get(
    "/studies/{study_fingerprint}",
    response_model=ResearchStudySummary | ResearchStudy,
)
async def get_research_study(
    study_fingerprint: str,
    catalog: Annotated[ResearchStudyCatalog, Depends(get_research_study_catalog)],
    publications: Annotated[StrategySnapshotStore, Depends(get_strategy_snapshot_store)],
    detail: Annotated[Literal["summary", "full"], Query()] = "summary",
) -> ResearchStudySummary | ResearchStudy:
    """Return one persisted study summary (default) or the full document.

    The summary names each row's and candidate's sweep ``axis_values`` (read from the
    candidate snapshots), window bounds, per-candidate OOS sums, and a thinned stitched
    OOS path (ADR 0094).
    """
    try:
        canonical = await catalog.load(study_fingerprint)
        study = ResearchStudy.model_validate_json(canonical)
    except StudyCatalogNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Published research study was not found.",
        ) from None
    except StudyCatalogUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Research study catalog is unavailable.",
        ) from None
    except StudyCatalogIntegrityError, ValidationError, ValueError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Research study catalog is unavailable.",
        ) from None
    if study.study_fingerprint != study_fingerprint:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Research study catalog is unavailable.",
        )
    if detail == "full":
        return study
    definitions = await load_candidate_definitions(publications, study)
    return summarize_research_study(study, definitions=definitions)


@router.get("/promotion-evidence", response_model=PromotionEvidence)
async def get_promotion_evidence(
    strategy_fingerprint: Annotated[str, Query()],
    publications: Annotated[StrategySnapshotStore, Depends(get_strategy_snapshot_store)],
    results: Annotated[BacktestResultReader, Depends(get_backtest_result_store)],
    catalog: Annotated[ResearchStudyCatalog, Depends(get_research_study_catalog)],
    execution: Annotated[ExecutionStore, Depends(get_execution_store)],
) -> PromotionEvidence:
    """Return IS vs OOS vs sweep vs paper vs live evidence for one strategy."""
    if not strategy_fingerprint.startswith("sha256:") or len(strategy_fingerprint) != 71:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "strategy_invalid", "message": "Strategy fingerprint is malformed."},
        )
    try:
        published = await publications.load(strategy_fingerprint)
    except StrategySnapshotError as error:
        message = str(error)
        if "not found" in message.lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Published strategy was not found.",
            ) from None
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Strategy publication is unavailable.",
        ) from None
    try:
        backtests = await results.list_summaries(
            strategy_fingerprint=strategy_fingerprint,
            limit=100,
            offset=0,
        )
    except Exception:  # noqa: BLE001
        backtests = ()
    studies = await _studies_for_strategy(catalog, strategy_fingerprint)
    try:
        deployments = await execution.list_by_strategy(str(published.definition.strategy_id))
    except ExecutionStoreError:
        deployments = ()
    return assemble_promotion_evidence(
        strategy_fingerprint=strategy_fingerprint,
        full_window_backtests=backtests,
        studies=studies,
        deployments=deployments,
    )


async def _studies_for_strategy(
    catalog: ResearchStudyCatalog,
    strategy_fingerprint: str,
) -> tuple[ResearchStudy, ...]:
    """Load catalog studies that mention the requested strategy fingerprint."""
    try:
        rows = await catalog.list_summaries(limit=100, offset=0)
    except StudyCatalogUnavailableError:
        return ()
    matched: list[ResearchStudy] = []
    for row in rows:
        try:
            study = ResearchStudy.model_validate_json(await catalog.load(row.study_fingerprint))
        except StudyCatalogUnavailableError, StudyCatalogNotFoundError, ValidationError:
            continue
        if any(window.strategy_fingerprint == strategy_fingerprint for window in study.windows):
            matched.append(study)
    return tuple(matched)
