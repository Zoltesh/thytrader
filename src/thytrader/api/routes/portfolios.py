"""Browser- and agent-facing HTTP contract for portfolios (ADR 0088).

A portfolio is a set of sleeves (one strategy each, with a capital weight) under shared
limits, with manager settings and an append-only journal. Every mutation is
revision-guarded (409 ``portfolio_revision_conflict``) and passes the application trust
boundary (installation auth, plus CSRF from a browser). Portfolio backtests are async jobs
(202, then poll). Nothing here deploys a portfolio or places orders.
"""

from __future__ import annotations

from datetime import UTC, datetime
import logging
from typing import TYPE_CHECKING, Annotated, Literal
from uuid import UUID  # noqa: TC003 - FastAPI resolves this annotation at runtime.

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status

from thytrader.api.dependencies import (
    get_audit_event_store,
    get_dataset_store,
    get_execution_store,
    get_portfolio_backtest_store,
    get_portfolio_store,
    get_strategy_store,
)
from thytrader.api.live_ack import LIVE_ACK_REQUIRED_DETAIL
from thytrader.api.strategy_http import strategy_http_error
from thytrader.execution.models import ExecutionStoreError
from thytrader.execution.store import ExecutionStore  # noqa: TC001 - FastAPI Depends.
from thytrader.market_data.datasets import DatasetStore  # noqa: TC001 - FastAPI Depends.
from thytrader.persistence.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventStore,
)
from thytrader.portfolios.backtest import (
    PortfolioBacktestJob,
    PortfolioBacktestRequest,
    downsample_curve,
)
from thytrader.portfolios.deployment import members, sleeve_books
from thytrader.portfolios.models import (
    MutationContext,
    PortfolioConflictError,
    PortfolioCreateRequest,
    PortfolioError,
    PortfolioLiveAcknowledgementError,
    PortfolioNotFoundError,
    PortfolioProposalNotFoundError,
    PortfolioRevisionConflictError,
    PortfolioSleeveExistsError,
    PortfolioSleeveNotFoundError,
    PortfolioStartRejectedError,
    PortfolioStrategyNotFoundError,
    PortfolioUpdateRequest,
    PortfolioValidationError,
    SetWeightsRequest,
    SleeveAddRequest,
    SleeveUpdateRequest,
)
from thytrader.portfolios.planning import PortfolioBacktestRejectedError, plan_portfolio_backtest
from thytrader.portfolios.store import (
    PortfolioBacktestNotFoundError,
    PortfolioBacktestStore,
    PortfolioStore,
)
from thytrader.portfolios.views import (
    JournalListResponse,
    PlannedSleeveResponse,
    PortfolioBacktestAcceptedResponse,
    PortfolioBacktestDetailResponse,
    PortfolioBacktestJobListResponse,
    PortfolioBacktestListResponse,
    PortfolioDeletionResponse,
    PortfolioListResponse,
    PortfolioResponse,
    portfolio_response,
)
from thytrader.research.pagination import decode_offset_cursor, encode_offset_cursor
from thytrader.strategies.library import StrategyLibraryError, StrategyStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thytrader.portfolios.models import PortfolioAggregate

router = APIRouter(prefix="/api/v1/portfolios", tags=["portfolios"])
_logger = logging.getLogger(__name__)
_FINGERPRINT = r"^sha256:[0-9a-f]{64}$"


@router.get("", response_model=PortfolioListResponse)
async def list_portfolios(
    store: Annotated[PortfolioStore, Depends(get_portfolio_store)],
    execution: Annotated[ExecutionStore, Depends(get_execution_store)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> PortfolioListResponse:
    """Return one page of portfolios (oldest first) with sleeves and allocation."""
    start = _cursor_offset(cursor)
    try:
        page = await store.list_page(limit=limit, offset=start)
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    has_more = start + len(page.portfolios) < page.total
    return PortfolioListResponse(
        portfolios=await _responses(page.portfolios, execution),
        limit=limit,
        returned=len(page.portfolios),
        total=page.total,
        has_more=has_more,
        next_cursor=encode_offset_cursor(start + limit) if has_more else None,
    )


@router.post("", response_model=PortfolioResponse, status_code=status.HTTP_201_CREATED)
async def create_portfolio(
    body: PortfolioCreateRequest,
    request: Request,
    store: Annotated[PortfolioStore, Depends(get_portfolio_store)],
    execution: Annotated[ExecutionStore, Depends(get_execution_store)],
) -> PortfolioResponse:
    """Create one paper or live portfolio. Mode and quote currency are then fixed."""
    try:
        created = await store.create(body, context=mutation_context(request))
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    return await _response(created, execution)


@router.get("/{portfolio_id}", response_model=PortfolioResponse)
async def get_portfolio(
    portfolio_id: UUID,
    store: Annotated[PortfolioStore, Depends(get_portfolio_store)],
    execution: Annotated[ExecutionStore, Depends(get_execution_store)],
) -> PortfolioResponse:
    """Return one portfolio with sleeves, allocation, limits, and manager settings."""
    try:
        aggregate = await store.get(portfolio_id)
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    return await _response(aggregate, execution)


@router.patch("/{portfolio_id}", response_model=PortfolioResponse)
async def update_portfolio(
    portfolio_id: UUID,
    body: PortfolioUpdateRequest,
    request: Request,
    store: Annotated[PortfolioStore, Depends(get_portfolio_store)],
    execution: Annotated[ExecutionStore, Depends(get_execution_store)],
) -> PortfolioResponse:
    """Change name, capital, cash reserve, limits, or manager settings (revision-guarded)."""
    try:
        updated = await store.update(portfolio_id, body, context=mutation_context(request))
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    return await _response(updated, execution)


@router.delete("/{portfolio_id}", response_model=PortfolioDeletionResponse)
async def delete_portfolio(
    portfolio_id: UUID,
    store: Annotated[PortfolioStore, Depends(get_portfolio_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
    revision: Annotated[int, Query(ge=1)],
) -> PortfolioDeletionResponse:
    """Delete one portfolio with its sleeves, journal, and backtests (revision-guarded)."""
    try:
        deletion = await store.delete(portfolio_id, expected_revision=revision)
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    await _audit(
        audit,
        action="delete_portfolio",
        detail=(
            f"portfolio_id={portfolio_id} sleeves={deletion.sleeves} "
            f"backtests={deletion.backtests} journal_entries={deletion.journal_entries}"
        ),
    )
    return PortfolioDeletionResponse(
        portfolio_id=deletion.portfolio_id,
        name=deletion.name,
        sleeves=deletion.sleeves,
        journal_entries=deletion.journal_entries,
        backtests=deletion.backtests,
        backtest_jobs=deletion.backtest_jobs,
    )


@router.post(
    "/{portfolio_id}/sleeves",
    response_model=PortfolioResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_sleeve(
    portfolio_id: UUID,
    body: SleeveAddRequest,
    request: Request,
    store: Annotated[PortfolioStore, Depends(get_portfolio_store)],
    execution: Annotated[ExecutionStore, Depends(get_execution_store)],
) -> PortfolioResponse:
    """Add one strategy as a sleeve (same quote currency; weights + reserve <= 1)."""
    try:
        updated = await store.add_sleeve(portfolio_id, body, context=mutation_context(request))
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    return await _response(updated, execution)


@router.patch("/{portfolio_id}/sleeves/{sleeve_id}", response_model=PortfolioResponse)
async def update_sleeve(
    portfolio_id: UUID,
    sleeve_id: UUID,
    body: SleeveUpdateRequest,
    request: Request,
    store: Annotated[PortfolioStore, Depends(get_portfolio_store)],
    execution: Annotated[ExecutionStore, Depends(get_execution_store)],
) -> PortfolioResponse:
    """Change one sleeve's weight and/or note (revision-guarded)."""
    try:
        updated = await store.update_sleeve(
            portfolio_id, sleeve_id, body, context=mutation_context(request)
        )
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    return await _response(updated, execution)


@router.delete("/{portfolio_id}/sleeves/{sleeve_id}", response_model=PortfolioResponse)
async def remove_sleeve(
    portfolio_id: UUID,
    sleeve_id: UUID,
    request: Request,
    store: Annotated[PortfolioStore, Depends(get_portfolio_store)],
    execution: Annotated[ExecutionStore, Depends(get_execution_store)],
    revision: Annotated[int, Query(ge=1)],
) -> PortfolioResponse:
    """Remove one sleeve (revision-guarded). Its strategy and evidence are untouched."""
    try:
        updated = await store.remove_sleeve(
            portfolio_id, sleeve_id, expected_revision=revision, context=mutation_context(request)
        )
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    return await _response(updated, execution)


@router.put("/{portfolio_id}/weights", response_model=PortfolioResponse)
async def set_weights(
    portfolio_id: UUID,
    body: SetWeightsRequest,
    request: Request,
    store: Annotated[PortfolioStore, Depends(get_portfolio_store)],
    execution: Annotated[ExecutionStore, Depends(get_execution_store)],
) -> PortfolioResponse:
    """Replace every sleeve weight (and optionally the cash reserve) in one revision."""
    try:
        updated = await store.set_weights(portfolio_id, body, context=mutation_context(request))
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    return await _response(updated, execution)


@router.get("/{portfolio_id}/journal", response_model=JournalListResponse)
async def list_journal(
    portfolio_id: UUID,
    store: Annotated[PortfolioStore, Depends(get_portfolio_store)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> JournalListResponse:
    """Return the portfolio journal newest first."""
    start = _cursor_offset(cursor)
    try:
        page = await store.journal(portfolio_id, limit=limit, offset=start)
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    has_more = start + len(page.entries) < page.total
    return JournalListResponse(
        entries=page.entries,
        limit=limit,
        returned=len(page.entries),
        total=page.total,
        has_more=has_more,
        next_cursor=encode_offset_cursor(start + limit) if has_more else None,
    )


@router.post(
    "/{portfolio_id}/backtests",
    response_model=PortfolioBacktestAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_portfolio_backtest(
    portfolio_id: UUID,
    body: PortfolioBacktestRequest,
    request: Request,
    store: Annotated[PortfolioStore, Depends(get_portfolio_store)],
    backtests: Annotated[PortfolioBacktestStore, Depends(get_portfolio_backtest_store)],
    strategies: Annotated[StrategyStore, Depends(get_strategy_store)],
    datasets: Annotated[DatasetStore, Depends(get_dataset_store)],
) -> PortfolioBacktestAcceptedResponse:
    """Snapshot every sleeve, fix one common window, and queue the portfolio backtest."""
    try:
        aggregate = await store.get(portfolio_id)
        plan = await plan_portfolio_backtest(
            aggregate, body, strategies=strategies, datasets=datasets
        )
        job = await backtests.create_job(plan, context=mutation_context(request))
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    except StrategyLibraryError as error:
        raise strategy_http_error(error) from None
    return PortfolioBacktestAcceptedResponse(
        job=job,
        sleeves=tuple(
            PlannedSleeveResponse(
                sleeve_id=sleeve.sleeve_id,
                strategy_id=sleeve.strategy_id,
                strategy_fingerprint=sleeve.strategy_fingerprint,
                capital_quote=sleeve.capital_quote,
                dataset_fingerprint=sleeve.submission.dataset_fingerprint,
            )
            for sleeve in plan.sleeves
        ),
    )


@router.get("/{portfolio_id}/backtests", response_model=PortfolioBacktestListResponse)
async def list_portfolio_backtests(
    portfolio_id: UUID,
    backtests: Annotated[PortfolioBacktestStore, Depends(get_portfolio_backtest_store)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
) -> PortfolioBacktestListResponse:
    """Return stored portfolio backtests newest first (summary rows, no curves)."""
    start = _cursor_offset(cursor)
    try:
        rows = await backtests.list_results(portfolio_id, limit=limit + 1, offset=start)
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    has_more = len(rows) > limit
    page = rows[:limit]
    return PortfolioBacktestListResponse(
        entries=page,
        limit=limit,
        offset=start,
        returned=len(page),
        has_more=has_more,
        next_cursor=encode_offset_cursor(start + limit) if has_more else None,
    )


@router.get("/{portfolio_id}/backtests/jobs", response_model=PortfolioBacktestJobListResponse)
async def list_portfolio_backtest_jobs(
    portfolio_id: UUID,
    backtests: Annotated[PortfolioBacktestStore, Depends(get_portfolio_backtest_store)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> PortfolioBacktestJobListResponse:
    """Return the portfolio's newest backtest jobs first."""
    try:
        jobs = await backtests.list_jobs(portfolio_id, limit=limit)
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    return PortfolioBacktestJobListResponse(jobs=jobs, limit=limit, returned=len(jobs))


@router.get("/{portfolio_id}/backtests/jobs/{job_id}", response_model=PortfolioBacktestJob)
async def get_portfolio_backtest_job(
    portfolio_id: UUID,
    job_id: UUID,
    backtests: Annotated[PortfolioBacktestStore, Depends(get_portfolio_backtest_store)],
) -> PortfolioBacktestJob:
    """Poll one portfolio backtest job (``result_fingerprint`` once completed)."""
    try:
        job = await backtests.get_job(portfolio_id, job_id)
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    if job is None:
        raise _error(
            status.HTTP_404_NOT_FOUND,
            "portfolio_backtest_job_not_found",
            "Portfolio backtest job was not found.",
        )
    return job


@router.get(
    "/{portfolio_id}/backtests/{result_fingerprint}",
    response_model=PortfolioBacktestDetailResponse,
)
async def get_portfolio_backtest(
    portfolio_id: UUID,
    result_fingerprint: Annotated[str, Path(pattern=_FINGERPRINT)],
    backtests: Annotated[PortfolioBacktestStore, Depends(get_portfolio_backtest_store)],
    max_points: Annotated[int | None, Query(ge=50, le=20000)] = None,
) -> PortfolioBacktestDetailResponse:
    """Return one stored portfolio backtest, re-verified against its fingerprint."""
    try:
        result = await backtests.load_result(portfolio_id, result_fingerprint)
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    total = len(result.equity_curve)
    curve = (
        result.equity_curve
        if max_points is None
        else downsample_curve(result.equity_curve, max_points)
    )
    return PortfolioBacktestDetailResponse(
        result_fingerprint=result_fingerprint,
        result=result.model_copy(update={"equity_curve": curve}),
        equity_curve_points=total,
        equity_curve_downsampled=len(curve) != total,
    )


async def _response(aggregate: PortfolioAggregate, execution: ExecutionStore) -> PortfolioResponse:
    """One portfolio body with its deployment state."""
    return (await _responses((aggregate,), execution))[0]


async def _responses(
    aggregates: Sequence[PortfolioAggregate], execution: ExecutionStore
) -> tuple[PortfolioResponse, ...]:
    """Portfolio bodies with their deployment states (one deployments read)."""
    try:
        deployments = await execution.list_deployments()
    except ExecutionStoreError:
        deployments = ()
    return tuple(
        portfolio_response(
            aggregate,
            deployment_state=sleeve_books(
                aggregate, members(deployments, aggregate.portfolio.portfolio_id)
            ).state,
        )
        for aggregate in aggregates
    )


def mutation_context(request: Request) -> MutationContext:
    """The operator acted now, from a browser (Origin present) or through the API."""
    channel: Literal["browser", "api"] = "browser" if request.headers.get("origin") else "api"
    return MutationContext(actor="operator", channel=channel, occurred_at=datetime.now(UTC))


_NOT_FOUND_CODES: tuple[tuple[type[PortfolioError], str], ...] = (
    (PortfolioNotFoundError, "portfolio_not_found"),
    (PortfolioSleeveNotFoundError, "portfolio_sleeve_not_found"),
    (PortfolioStrategyNotFoundError, "strategy_not_found"),
    (PortfolioBacktestNotFoundError, "portfolio_backtest_not_found"),
    (PortfolioProposalNotFoundError, "portfolio_proposal_not_found"),
)


def portfolio_http_error(error: PortfolioError) -> HTTPException:
    """Map one redacted portfolio failure onto its stable HTTP status and code."""
    for error_type, code in _NOT_FOUND_CODES:
        if isinstance(error, error_type):
            return _error(status.HTTP_404_NOT_FOUND, code, str(error))
    if isinstance(error, PortfolioLiveAcknowledgementError):
        return HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED, detail=LIVE_ACK_REQUIRED_DETAIL
        )
    if isinstance(error, PortfolioConflictError):
        return _error(status.HTTP_409_CONFLICT, error.code, str(error))
    if isinstance(error, PortfolioStartRejectedError):
        return _start_rejected(error)
    if isinstance(error, PortfolioRevisionConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "portfolio_revision_conflict",
                "message": str(error),
                "current_revision": error.current_revision,
            },
        )
    if isinstance(error, PortfolioSleeveExistsError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": error.code, "message": str(error), "sleeve_id": str(error.sleeve_id)},
        )
    if isinstance(error, PortfolioBacktestRejectedError):
        return _rejected(error)
    if isinstance(error, PortfolioValidationError):
        return _error(status.HTTP_422_UNPROCESSABLE_CONTENT, error.code, str(error))
    _logger.warning("portfolio_storage_unavailable error=%s", type(error).__name__)
    return _error(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "portfolio_storage_unavailable",
        "Portfolio storage is unavailable.",
    )


def _rejected(error: PortfolioBacktestRejectedError) -> HTTPException:
    """422 with every sleeve problem that blocked the portfolio backtest."""
    problems = [
        {
            "code": problem.code,
            "message": problem.message,
            "sleeve_id": _optional_text(problem.sleeve_id),
            "strategy_id": _optional_text(problem.strategy_id),
            "strategy_name": problem.strategy_name,
        }
        for problem in error.problems
    ]
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": "portfolio_backtest_rejected", "message": str(error), "problems": problems},
    )


def _start_rejected(error: PortfolioStartRejectedError) -> HTTPException:
    """422 with every sleeve problem that blocked starting the portfolio."""
    problems = [
        {
            "code": problem.code,
            "message": problem.message,
            "sleeve_id": _optional_text(problem.sleeve_id),
            "strategy_id": _optional_text(problem.strategy_id),
            "strategy_name": problem.strategy_name,
        }
        for problem in error.problems
    ]
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": error.code, "message": str(error), "problems": problems},
    )


def _optional_text(value: UUID | None) -> str | None:
    """Render an optional identity."""
    return None if value is None else str(value)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    """Build one ``{"detail": {"code", "message"}}`` error."""
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _cursor_offset(cursor: str | None) -> int:
    """Decode one opaque offset cursor."""
    if cursor is None:
        return 0
    try:
        return decode_offset_cursor(cursor)
    except ValueError:
        raise _error(
            status.HTTP_400_BAD_REQUEST,
            "portfolio_cursor_invalid",
            "Pagination cursor is malformed.",
        ) from None


async def _audit(audit: AuditEventStore, *, action: str, detail: str) -> None:
    """Record a committed change; an audit outage never reverses or hides it."""
    event = AuditEvent(
        occurred_at=datetime.now(UTC),
        category=AuditEventCategory.RESEARCH,
        action=action,
        outcome=AuditEventOutcome.SUCCESS,
        detail=detail,
    )
    try:
        await audit.append(event)
    except Exception as error:  # noqa: BLE001 - the change already committed.
        _logger.warning("portfolio_audit_failed error=%s", type(error).__name__)
