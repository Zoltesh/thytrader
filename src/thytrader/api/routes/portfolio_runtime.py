"""HTTP contract for deploying portfolios and for the manager's proposals (ADR 0091).

Deployment control: read a portfolio's deployment; start (or attach) one bot per sleeve;
pause, resume, and stop the whole portfolio or one sleeve; reset a latched portfolio
breaker. Live start, live resume, and approving a live resume need ``i_understand_live``
(HTTP 428 otherwise). The manager loop: submit a proposal (rebalance, pause or resume a
sleeve, add a sleeve) with rationale and evidence; a person approves or declines it; read
the one-call manager briefing. No route places an order: strategies place every trade.
Every mutation passes the application trust boundary (installation auth; CSRF from a
browser) and is journaled.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from thytrader.api.dependencies import (
    get_audit_event_store,
    get_decision_journal_store,
    get_execution_store,
    get_market_data_watchlist_store,
    get_optional_decision_journal_store,
    get_portfolio_storage,
    get_risk_policy_store,
    get_runtime_state,
    get_strategy_snapshot_store,
    get_strategy_store,
)
from thytrader.api.paper_fees import get_paper_fee_source
from thytrader.api.routes.portfolios import mutation_context, portfolio_http_error
from thytrader.data_control.service import ingestion_provider
from thytrader.execution.book_marks import marks_by_deployment
from thytrader.execution.decision_store import (
    DecisionJournalStore,
)
from thytrader.execution.models import ExecutionConflictError, ExecutionStoreError
from thytrader.execution.service import ReferenceWatchlist, parse_decimal
from thytrader.execution.store import ExecutionStore  # noqa: TC001 - FastAPI Depends.
from thytrader.market_data.watchlist import (
    MarketDataWatchlistStore,  # noqa: TC001 - FastAPI Depends.
)
from thytrader.persistence.audit_events import AuditEventStore  # noqa: TC001 - FastAPI Depends.
from thytrader.portfolios.briefing import (
    DEFAULT_DECISIONS_PER_SLEEVE,
    DEFAULT_JOURNAL_ENTRIES,
    ManagerBriefing,
    build_manager_briefing,
)
from thytrader.portfolios.manager import ProposalService
from thytrader.portfolios.models import JournalChannel, PortfolioError, RevisionNumber
from thytrader.portfolios.proposals import (
    ProposalDecisionRequest,
    ProposalStatus,
    ProposalSubmitRequest,
)
from thytrader.portfolios.runtime import FeeAssumptions, PortfolioRuntimeService
from thytrader.portfolios.runtime_views import (
    PortfolioActionResponse,
    PortfolioDeploymentResponse,
    ProposalListResponse,
    ProposalResponse,
    action_response,
    deployment_response,
)
from thytrader.portfolios.store import PortfolioStorage
from thytrader.research.pagination import decode_offset_cursor, encode_offset_cursor
from thytrader.risk.store import RiskPolicyStore  # noqa: TC001 - FastAPI Depends.
from thytrader.runtime import RuntimeState  # noqa: TC001 - FastAPI Depends.
from thytrader.strategies.library import StrategyStore  # noqa: TC001 - FastAPI Depends.
from thytrader.strategies.snapshots import (
    StrategySnapshotStore,  # noqa: TC001 - FastAPI Depends.
)

if TYPE_CHECKING:
    from decimal import Decimal

router = APIRouter(prefix="/api/v1/portfolios", tags=["portfolio deployment"])


class PortfolioStartRequest(BaseModel):
    """Start every sleeve (or one) of the portfolio at the revision you reviewed."""

    model_config = ConfigDict(extra="forbid")

    revision: RevisionNumber
    i_understand_live: StrictBool = Field(
        default=False,
        description=(
            "Required true on a live portfolio (HTTP 428 live_acknowledgement_required "
            "otherwise). Send only after the operator explicitly acknowledged live trading."
        ),
    )
    maker_fee_rate: str | None = Field(default=None, description="Paper only, with taker.")
    taker_fee_rate: str | None = Field(default=None, description="Paper only, with maker.")


class PortfolioResumeRequest(BaseModel):
    """Resume paused sleeves; a live portfolio needs the explicit live acknowledgement."""

    model_config = ConfigDict(extra="forbid")

    i_understand_live: StrictBool = False


def runtime_service(
    portfolios: Annotated[PortfolioStorage, Depends(get_portfolio_storage)],
    execution: Annotated[ExecutionStore, Depends(get_execution_store)],
    strategies: Annotated[StrategyStore, Depends(get_strategy_store)],
    publication: Annotated[StrategySnapshotStore, Depends(get_strategy_snapshot_store)],
    risk_store: Annotated[RiskPolicyStore, Depends(get_risk_policy_store)],
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
    watchlist: Annotated[MarketDataWatchlistStore, Depends(get_market_data_watchlist_store)],
) -> PortfolioRuntimeService:
    """The portfolio runtime service over this request's stores."""
    return PortfolioRuntimeService(
        portfolios=portfolios,
        execution=execution,
        strategies=strategies,
        publication=publication,
        risk_store=risk_store,
        live_allowed=runtime.settings.coinbase_api_key_name is not None,
        audit=audit,
        reference_watches=ReferenceWatchlist(
            store=watchlist, provider=ingestion_provider(runtime.settings)
        ),
    )


def proposal_service(
    portfolios: Annotated[PortfolioStorage, Depends(get_portfolio_storage)],
    execution: Annotated[ExecutionStore, Depends(get_execution_store)],
    runtime: Annotated[PortfolioRuntimeService, Depends(runtime_service)],
) -> ProposalService:
    """The proposal service over this request's stores."""
    return ProposalService(portfolios=portfolios, execution=execution, runtime=runtime)


RuntimeDep = Annotated[PortfolioRuntimeService, Depends(runtime_service)]
ProposalsDep = Annotated[ProposalService, Depends(proposal_service)]
StorageDep = Annotated[PortfolioStorage, Depends(get_portfolio_storage)]


@router.get("/{portfolio_id}/deployment", response_model=PortfolioDeploymentResponse)
async def get_deployment(
    portfolio_id: UUID,
    service: RuntimeDep,
    storage: StorageDep,
    journal: Annotated[DecisionJournalStore | None, Depends(get_optional_decision_journal_store)],
) -> PortfolioDeploymentResponse:
    """The portfolio's deployment: state, each sleeve's bot, breakers, and exposure.

    Each sleeve bot's open books carry a last-bar mark and gross unrealized PnL (ADR 0098).
    """
    try:
        snapshot = await service.snapshot(portfolio_id)
        pending = await _pending_count(storage, portfolio_id)
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    except ExecutionStoreError:
        raise _execution_unavailable() from None
    marks = None if journal is None else await marks_by_deployment(journal, snapshot.snapshots)
    return deployment_response(snapshot, pending_proposals=pending, marks=marks)


@router.post("/{portfolio_id}/start", response_model=PortfolioActionResponse)
async def start_portfolio(
    portfolio_id: UUID,
    body: PortfolioStartRequest,
    request: Request,
    service: RuntimeDep,
    storage: StorageDep,
) -> PortfolioActionResponse:
    """Start (or attach) one bot per sleeve; nothing starts if any sleeve is refused."""
    return await _start(portfolio_id, body, request, service, storage, sleeve_id=None)


@router.post("/{portfolio_id}/sleeves/{sleeve_id}/start", response_model=PortfolioActionResponse)
async def start_sleeve(
    portfolio_id: UUID,
    sleeve_id: UUID,
    body: PortfolioStartRequest,
    request: Request,
    service: RuntimeDep,
    storage: StorageDep,
) -> PortfolioActionResponse:
    """Start one sleeve's bot (weight times capital), for example after adding it."""
    return await _start(portfolio_id, body, request, service, storage, sleeve_id=sleeve_id)


@router.post("/{portfolio_id}/pause", response_model=PortfolioActionResponse)
async def pause_portfolio(
    portfolio_id: UUID, request: Request, service: RuntimeDep, storage: StorageDep
) -> PortfolioActionResponse:
    """Pause every running sleeve: no new entries; exits and protection continue."""
    return await _action(
        "pause", portfolio_id, request, service, storage, sleeve_id=None, live_ack=False
    )


@router.post("/{portfolio_id}/sleeves/{sleeve_id}/pause", response_model=PortfolioActionResponse)
async def pause_sleeve(
    portfolio_id: UUID,
    sleeve_id: UUID,
    request: Request,
    service: RuntimeDep,
    storage: StorageDep,
) -> PortfolioActionResponse:
    """Pause one sleeve's bot."""
    return await _action(
        "pause", portfolio_id, request, service, storage, sleeve_id=sleeve_id, live_ack=False
    )


@router.post("/{portfolio_id}/resume", response_model=PortfolioActionResponse)
async def resume_portfolio(
    portfolio_id: UUID,
    request: Request,
    service: RuntimeDep,
    storage: StorageDep,
    body: Annotated[PortfolioResumeRequest | None, Body()] = None,
) -> PortfolioActionResponse:
    """Resume every paused sleeve (live re-arms orders and needs ``i_understand_live``)."""
    acknowledged = body.i_understand_live if body is not None else False
    return await _action(
        "resume", portfolio_id, request, service, storage, sleeve_id=None, live_ack=acknowledged
    )


@router.post("/{portfolio_id}/sleeves/{sleeve_id}/resume", response_model=PortfolioActionResponse)
async def resume_sleeve(
    portfolio_id: UUID,
    sleeve_id: UUID,
    request: Request,
    service: RuntimeDep,
    storage: StorageDep,
    body: Annotated[PortfolioResumeRequest | None, Body()] = None,
) -> PortfolioActionResponse:
    """Resume one paused sleeve (live needs ``i_understand_live``)."""
    acknowledged = body.i_understand_live if body is not None else False
    return await _action(
        "resume",
        portfolio_id,
        request,
        service,
        storage,
        sleeve_id=sleeve_id,
        live_ack=acknowledged,
    )


@router.post("/{portfolio_id}/stop", response_model=PortfolioActionResponse)
async def stop_portfolio(
    portfolio_id: UUID,
    request: Request,
    service: RuntimeDep,
    storage: StorageDep,
    flatten: Annotated[
        bool,
        Query(
            description=(
                "True exits every sleeve's positions at market, then cancels remainders. The "
                "default managed stop keeps protective orders until positions close."
            )
        ),
    ] = False,
) -> PortfolioActionResponse:
    """Stop every sleeve: managed stop by default, or flatten."""
    return await _action(
        "stop",
        portfolio_id,
        request,
        service,
        storage,
        sleeve_id=None,
        live_ack=False,
        flatten=flatten,
    )


@router.post("/{portfolio_id}/sleeves/{sleeve_id}/stop", response_model=PortfolioActionResponse)
async def stop_sleeve(
    portfolio_id: UUID,
    sleeve_id: UUID,
    request: Request,
    service: RuntimeDep,
    storage: StorageDep,
    flatten: Annotated[bool, Query()] = False,
) -> PortfolioActionResponse:
    """Stop one sleeve's bot: managed stop by default, or flatten."""
    return await _action(
        "stop",
        portfolio_id,
        request,
        service,
        storage,
        sleeve_id=sleeve_id,
        live_ack=False,
        flatten=flatten,
    )


@router.post("/{portfolio_id}/breaker/reset", response_model=PortfolioDeploymentResponse)
async def reset_breaker(
    portfolio_id: UUID, request: Request, service: RuntimeDep, storage: StorageDep
) -> PortfolioDeploymentResponse:
    """Clear a latched portfolio breaker (sleeves stay paused until resumed)."""
    try:
        await service.reset_breaker(portfolio_id, context=mutation_context(request))
        snapshot = await service.snapshot(portfolio_id)
        pending = await _pending_count(storage, portfolio_id)
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    except ExecutionStoreError:
        raise _execution_unavailable() from None
    return deployment_response(snapshot, pending_proposals=pending)


@router.get("/{portfolio_id}/proposals", response_model=ProposalListResponse)
async def list_proposals(
    portfolio_id: UUID,
    service: ProposalsDep,
    status_filter: Annotated[
        Literal["pending", "applied", "declined", "failed", "expired"] | None,
        Query(alias="status"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query()] = None,
) -> ProposalListResponse:
    """The manager's proposals newest first (optionally one status)."""
    start = _cursor_offset(cursor)
    status_value: ProposalStatus | None = status_filter
    try:
        page = await service.list_proposals(
            portfolio_id, status=status_value, limit=limit, offset=start
        )
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    has_more = start + len(page.proposals) < page.total
    return ProposalListResponse(
        proposals=page.proposals,
        limit=limit,
        returned=len(page.proposals),
        total=page.total,
        has_more=has_more,
        next_cursor=encode_offset_cursor(start + limit) if has_more else None,
    )


@router.post(
    "/{portfolio_id}/proposals",
    response_model=ProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_proposal(
    portfolio_id: UUID, body: ProposalSubmitRequest, request: Request, service: ProposalsDep
) -> ProposalResponse:
    """The manager submits a proposal; it auto-applies only inside its permissions."""
    try:
        outcome = await service.submit(portfolio_id, body, channel=_channel(request))
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    except ExecutionStoreError:
        raise _execution_unavailable() from None
    return ProposalResponse(
        proposal=outcome.proposal, portfolio_revision=outcome.aggregate.portfolio.revision
    )


@router.get("/{portfolio_id}/proposals/{proposal_id}", response_model=ProposalResponse)
async def get_proposal(
    portfolio_id: UUID, proposal_id: UUID, service: ProposalsDep, storage: StorageDep
) -> ProposalResponse:
    """One proposal with the portfolio's current revision."""
    try:
        proposal = await service.get(portfolio_id, proposal_id)
        aggregate = await storage.get(portfolio_id)
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    return ProposalResponse(proposal=proposal, portfolio_revision=aggregate.portfolio.revision)


@router.post("/{portfolio_id}/proposals/{proposal_id}/approve", response_model=ProposalResponse)
async def approve_proposal(
    portfolio_id: UUID,
    proposal_id: UUID,
    request: Request,
    service: ProposalsDep,
    body: Annotated[ProposalDecisionRequest | None, Body()] = None,
) -> ProposalResponse:
    """A person approves a pending proposal; the change is made or the proposal fails."""
    try:
        outcome = await service.approve(
            portfolio_id,
            proposal_id,
            body or ProposalDecisionRequest(),
            context=mutation_context(request),
        )
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    except ExecutionStoreError:
        raise _execution_unavailable() from None
    return ProposalResponse(
        proposal=outcome.proposal, portfolio_revision=outcome.aggregate.portfolio.revision
    )


@router.post("/{portfolio_id}/proposals/{proposal_id}/decline", response_model=ProposalResponse)
async def decline_proposal(
    portfolio_id: UUID,
    proposal_id: UUID,
    request: Request,
    service: ProposalsDep,
    body: Annotated[ProposalDecisionRequest | None, Body()] = None,
) -> ProposalResponse:
    """A person declines a pending proposal; nothing changes."""
    try:
        outcome = await service.decline(
            portfolio_id,
            proposal_id,
            body or ProposalDecisionRequest(),
            context=mutation_context(request),
        )
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    return ProposalResponse(
        proposal=outcome.proposal, portfolio_revision=outcome.aggregate.portfolio.revision
    )


@router.get("/{portfolio_id}/briefing", response_model=ManagerBriefing)
async def get_briefing(
    portfolio_id: UUID,
    service: RuntimeDep,
    storage: StorageDep,
    decisions: Annotated[DecisionJournalStore, Depends(get_decision_journal_store)],
    decisions_per_sleeve: Annotated[int, Query(ge=0, le=50)] = DEFAULT_DECISIONS_PER_SLEEVE,
    journal_limit: Annotated[int, Query(ge=1, le=200)] = DEFAULT_JOURNAL_ENTRIES,
) -> ManagerBriefing:
    """Read-only: everything a manager agent reasons from, in one call."""
    try:
        await storage.expire_proposals(datetime.now(UTC))
        snapshot = await service.snapshot(portfolio_id)
        return await build_manager_briefing(
            snapshot,
            portfolios=storage,
            decisions=decisions,
            decisions_per_sleeve=decisions_per_sleeve,
            journal_entries=journal_limit,
        )
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    except ExecutionStoreError:
        raise _execution_unavailable() from None


async def _start(
    portfolio_id: UUID,
    body: PortfolioStartRequest,
    request: Request,
    service: PortfolioRuntimeService,
    storage: PortfolioStorage,
    *,
    sleeve_id: UUID | None,
) -> PortfolioActionResponse:
    """Start the portfolio (or one sleeve) and answer with the outcomes."""
    try:
        fees = FeeAssumptions(
            maker_fee_rate=_fee(body.maker_fee_rate, "maker_fee_rate"),
            taker_fee_rate=_fee(body.taker_fee_rate, "taker_fee_rate"),
        )
        result = await service.start(
            portfolio_id,
            revision=body.revision,
            context=mutation_context(request),
            live_acknowledged=body.i_understand_live,
            sleeve_id=sleeve_id,
            fees=fees,
            paper_fee_source=get_paper_fee_source(request),
        )
        snapshot = await service.snapshot(portfolio_id)
        pending = await _pending_count(storage, portfolio_id)
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    except ExecutionStoreError:
        raise _execution_unavailable() from None
    return action_response(result, snapshot, pending_proposals=pending)


async def _action(
    action: Literal["pause", "resume", "stop"],
    portfolio_id: UUID,
    request: Request,
    service: PortfolioRuntimeService,
    storage: PortfolioStorage,
    *,
    sleeve_id: UUID | None,
    live_ack: bool,
    flatten: bool = False,
) -> PortfolioActionResponse:
    """Run one pause, resume, or stop and answer with the outcomes."""
    context = mutation_context(request)
    try:
        if action == "pause":
            result = await service.pause(portfolio_id, context=context, sleeve_id=sleeve_id)
        elif action == "resume":
            result = await service.resume(
                portfolio_id, context=context, live_acknowledged=live_ack, sleeve_id=sleeve_id
            )
        else:
            result = await service.stop(
                portfolio_id, context=context, sleeve_id=sleeve_id, flatten=flatten
            )
        snapshot = await service.snapshot(portfolio_id)
        pending = await _pending_count(storage, portfolio_id)
    except PortfolioError as error:
        raise portfolio_http_error(error) from None
    except ExecutionStoreError:
        raise _execution_unavailable() from None
    return action_response(result, snapshot, pending_proposals=pending)


async def _pending_count(storage: PortfolioStorage, portfolio_id: UUID) -> int:
    """How many proposals wait for a decision."""
    page = await storage.list_proposals(portfolio_id, status="pending", limit=1, offset=0)
    return page.total


def _fee(value: str | None, field: str) -> Decimal | None:
    """Parse one optional paper fee rate (422 when it is not a decimal)."""
    try:
        return parse_decimal(value, field=field)
    except ExecutionConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "portfolio_fee_rates_invalid", "message": str(error)},
        ) from None


def _channel(request: Request) -> JournalChannel:
    """``browser`` when the request carried an Origin, else ``api``."""
    return "browser" if request.headers.get("origin") else "api"


def _execution_unavailable() -> HTTPException:
    """503 when the deployments cannot be read or written."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "execution_storage_unavailable",
            "message": "Execution storage is unavailable.",
        },
    )


def _cursor_offset(cursor: str | None) -> int:
    """Decode one opaque offset cursor."""
    if cursor is None:
        return 0
    try:
        return decode_offset_cursor(cursor)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "portfolio_cursor_invalid",
                "message": "Pagination cursor is malformed.",
            },
        ) from None
