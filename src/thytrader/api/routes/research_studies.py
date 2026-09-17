"""Research-study, template, and engine-support HTTP presentation.

These routes compose existing backtest engines. They cannot mutate published
results or grant paper/live trading authority.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal
from uuid import UUID  # noqa: TC003 - FastAPI path parameter binding

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, ValidationError

from thytrader.api.dependencies import (
    get_backtest_result_store,
    get_backtest_submitter,
    get_research_job_store,
    get_research_study_catalog,
    get_strategy_publication_store,
)
from thytrader.backtest.submission import (
    BacktestSubmissionError,
    BacktestSubmissionRejectedError,
    BacktestSubmitter,
)
from thytrader.persistence.backtest_results import BacktestResultReader  # noqa: TC001
from thytrader.research.catalog import (
    ResearchStudyCatalog,
    StudyCatalogIntegrityError,
    StudyCatalogNotFoundError,
    StudyCatalogSummary,
    StudyCatalogUnavailableError,
)
from thytrader.research.engine_support import EngineSupportMatrix, engine_support_matrix
from thytrader.research.jobs import ResearchJobAcceptedResponse, ResearchJobRecord, ResearchJobStore
from thytrader.research.studies import (
    ResearchStudy,
    ResearchStudyError,
    ResearchStudyPlan,
    ResearchStudyPlanSummary,
    ResearchStudyRequest,
    ResearchStudyService,
    ResearchStudySummary,
    StudyKind,
    StudyPlanningError,
    summarize_research_study,
    summarize_research_study_plan,
)
from thytrader.strategies.publication import (
    StrategyPublicationError,
    StrategyPublicationStore,
)
from thytrader.strategies.templates import template_catalog

router = APIRouter(prefix="/api/v1/research", tags=["research"])
_logger = logging.getLogger(__name__)


class StrategyTemplateEntry(BaseModel):
    """One fail-closed draft template identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    name: str
    description: str


class StrategyTemplateListResponse(BaseModel):
    """Catalog of research draft templates."""

    templates: tuple[StrategyTemplateEntry, ...]


class StudyCatalogListResponse(BaseModel):
    """Newest-first persisted study catalog rows."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    studies: tuple[StudyCatalogSummary, ...]


def _study_service(
    publications: Annotated[StrategyPublicationStore, Depends(get_strategy_publication_store)],
    submitter: Annotated[BacktestSubmitter, Depends(get_backtest_submitter)],
    results: Annotated[BacktestResultReader, Depends(get_backtest_result_store)],
    catalog: Annotated[ResearchStudyCatalog, Depends(get_research_study_catalog)],
) -> ResearchStudyService:
    """Compose studies from the same publication and backtest services as single runs."""
    return ResearchStudyService(
        publications=publications,
        submitter=submitter,
        results=results,
        catalog=catalog,
    )


@router.get("/engine-support", response_model=EngineSupportMatrix)
def get_engine_support() -> EngineSupportMatrix:
    """Return the V1/V2/V3 engine-support matrix without trading authority."""
    return engine_support_matrix()


@router.get("/templates", response_model=StrategyTemplateListResponse)
def get_strategy_templates() -> StrategyTemplateListResponse:
    """List fail-closed draft templates agents and the UI can create."""
    entries = tuple(
        StrategyTemplateEntry(id=item["id"], name=item["name"], description=item["description"])
        for item in template_catalog()
    )
    return StrategyTemplateListResponse(templates=entries)


@router.post(
    "/studies/plan",
    response_model=ResearchStudyPlanSummary | ResearchStudyPlan,
)
async def plan_research_study(
    request: ResearchStudyRequest,
    service: Annotated[ResearchStudyService, Depends(_study_service)],
    detail: Annotated[Literal["summary", "full"], Query()] = "summary",
) -> ResearchStudyPlanSummary | ResearchStudyPlan:
    """Return a compact plan summary (default) or the full window schedule."""
    try:
        plan = await service.plan(request)
    except StudyPlanningError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "study_window_rejected", "message": str(error)},
        ) from None
    except (ResearchStudyError, StrategyPublicationError) as error:
        _logger.warning("research_study_plan_failed error_class=%s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Research study planning is unavailable.",
        ) from None
    if detail == "full":
        return plan
    return summarize_research_study_plan(plan)


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
    request: ResearchStudyRequest,
    service: Annotated[ResearchStudyService, Depends(_study_service)],
    job_store: Annotated[ResearchJobStore, Depends(get_research_job_store)],
    async_submission: Annotated[bool, Query(alias="async")] = False,
) -> ResearchStudy | Response:
    """Submit or reuse child backtests and return the derived study document."""
    if async_submission:
        record = await job_store.create_study(request)
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
        return await service.submit(request)
    except StudyPlanningError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "study_window_rejected", "message": str(error)},
        ) from None
    except BacktestSubmissionRejectedError as rejected:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "backtest_window_rejected", "message": str(rejected)},
        ) from None
    except (ResearchStudyError, BacktestSubmissionError, StrategyPublicationError) as error:
        _logger.warning("research_study_submit_failed error_class=%s", type(error).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Research study submission is unavailable.",
        ) from None


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
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> StudyCatalogListResponse:
    """List persisted study catalog rows without child equity curves."""
    try:
        rows = await catalog.list_summaries(
            kind=kind.value if kind is not None else None,
            limit=limit,
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
    detail: Annotated[Literal["summary", "full"], Query()] = "summary",
) -> ResearchStudySummary | ResearchStudy:
    """Return one persisted study summary (default) or the full document."""
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
    return summarize_research_study(study)
