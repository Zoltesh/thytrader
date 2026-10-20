"""Agent desk HTTP routes (ADR 0147).

``GET .../briefing`` is what a harness reads each wake; ``POST .../wakes`` hands ThyTrader the
model's raw answer, which is parsed, checked against the desk caps, applied to paper books and
journaled. Desks are paper only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from thytrader.agent_desk.briefing import DeskBriefing
from thytrader.agent_desk.models import (
    MAX_MANDATE_CHARS,
    MAX_PRODUCTS,
    MAX_RESPONSE_CHARS,
    Desk,
    DeskBook,
    DeskCaps,
    DeskStatus,
    DeskWake,
)
from thytrader.agent_desk.score import DeskScore
from thytrader.agent_desk.service import (
    DeskError,
    DeskSpec,
    briefing,
    create_desk,
    record_wake,
    save_desk as save_desk_revision,
    score,
)
from thytrader.agent_desk.store import DeskConflictError, DeskStore, DeskStoreUnavailableError
from thytrader.api.dependencies import (
    get_audit_event_store,
    get_desk_store,
    get_execution_store,
    get_market_data_service,
    get_memory_store,
    get_paper_broker,
    get_risk_policy_store,
)
from thytrader.api.desk_execution import ApiDeskExecution
from thytrader.api.paper_fees import get_paper_fee_source
from thytrader.audit_events import AuditEventStore  # noqa: TC001
from thytrader.execution.broker import Broker  # noqa: TC001
from thytrader.execution.paper_fees import PaperFeeSource  # noqa: TC001
from thytrader.market_data.service import MarketDataService
from thytrader.memory.store import ExperientialMemoryStore  # noqa: TC001
from thytrader.risk.store import RiskPolicyStore  # noqa: TC001
from thytrader.trading.store import ExecutionStore  # noqa: TC001

router = APIRouter(prefix="/api/v1/desks", tags=["agent desks"])


class CreateDeskBody(BaseModel):
    """A new paper desk."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    quote_currency: Literal["USD", "USDC", "USDT"]
    starting_cash: Decimal = Field(gt=0)
    products: tuple[str, ...] = Field(min_length=1, max_length=MAX_PRODUCTS)
    timeframe: str = "5m"
    cadence_minutes: int = Field(default=30, ge=5, le=1440)
    harness: str = Field(default="claude", pattern=r"^[a-z][a-z0-9_-]{1,31}$")
    model: str | None = Field(default=None, max_length=80)
    mandate: str = Field(default="", max_length=MAX_MANDATE_CHARS)
    caps: DeskCaps = DeskCaps()


class WakeBody(BaseModel):
    """One model answer for the desk to apply and journal."""

    model_config = ConfigDict(extra="forbid")

    raw_response: str = Field(max_length=MAX_RESPONSE_CHARS * 5)
    harness: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,31}$")
    model: str | None = Field(default=None, max_length=80)
    briefing_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    started_at: datetime | None = None
    harness_error: str | None = Field(default=None, max_length=2000)


class StatusBody(BaseModel):
    """Pause, resume or retire a desk (open books are left as they are)."""

    model_config = ConfigDict(extra="forbid")

    status: DeskStatus


class DeskDetail(BaseModel):
    """A desk with its books."""

    desk: Desk
    books: tuple[DeskBook, ...]


def _execution(
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
    paper_broker: Annotated[Broker, Depends(get_paper_broker)],
    market_data: Annotated[MarketDataService, Depends(get_market_data_service)],
    risk_store: Annotated[RiskPolicyStore, Depends(get_risk_policy_store)],
    memory_store: Annotated[ExperientialMemoryStore, Depends(get_memory_store)],
    paper_fee_source: Annotated[PaperFeeSource, Depends(get_paper_fee_source)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
) -> ApiDeskExecution:
    """Bind the paper book operations for this request."""
    return ApiDeskExecution(
        store=store,
        paper_broker=paper_broker,
        market_data=market_data,
        risk_store=risk_store,
        memory_store=memory_store,
        paper_fee_source=paper_fee_source,
        audit=audit,
    )


@router.post("", response_model=Desk, status_code=status.HTTP_201_CREATED)
async def post_desk(
    body: CreateDeskBody, desks: Annotated[DeskStore, Depends(get_desk_store)]
) -> Desk:
    """Create a paper desk with its pool, universe, cadence, harness and caps."""
    spec = DeskSpec(**body.model_dump(exclude={"caps"}), caps=body.caps)
    try:
        return await create_desk(desks, spec, now=datetime.now(UTC))
    except DeskError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from None
    except DeskStoreUnavailableError as error:
        raise _unavailable(error) from None


@router.get("", response_model=tuple[Desk, ...])
async def get_desks(desks: Annotated[DeskStore, Depends(get_desk_store)]) -> tuple[Desk, ...]:
    """Every desk, newest first."""
    try:
        return await desks.list_desks()
    except DeskStoreUnavailableError as error:
        raise _unavailable(error) from None


@router.get("/{desk_id}", response_model=DeskDetail)
async def get_desk(
    desk_id: str, desks: Annotated[DeskStore, Depends(get_desk_store)]
) -> DeskDetail:
    """One desk and every book it funded."""
    try:
        desk = await desks.get_desk(desk_id)
        if desk is None:
            raise HTTPException(status_code=404, detail=f"DESK_NOT_FOUND: no desk {desk_id}.")
        return DeskDetail(desk=desk, books=await desks.list_books(desk_id))
    except DeskStoreUnavailableError as error:
        raise _unavailable(error) from None


@router.get("/{desk_id}/briefing")
async def get_briefing(
    desk_id: str,
    desks: Annotated[DeskStore, Depends(get_desk_store)],
    execution: Annotated[ApiDeskExecution, Depends(_execution)],
    market_data: Annotated[MarketDataService, Depends(get_market_data_service)],
) -> DeskBriefing:
    """Settle finished books and return the briefing, including the harness prompt."""
    try:
        return await briefing(desk_id, store=desks, execution=execution, market_data=market_data)
    except DeskError as error:
        raise _desk_http(error) from None
    except DeskStoreUnavailableError as error:
        raise _unavailable(error) from None


@router.post("/{desk_id}/wakes", response_model=DeskWake, status_code=status.HTTP_201_CREATED)
async def post_wake(
    desk_id: str,
    body: WakeBody,
    desks: Annotated[DeskStore, Depends(get_desk_store)],
    execution: Annotated[ApiDeskExecution, Depends(_execution)],
    market_data: Annotated[MarketDataService, Depends(get_market_data_service)],
) -> DeskWake:
    """Apply one answer under the desk caps and journal the wake (a refusal is a 201 too)."""
    try:
        return await record_wake(
            desk_id,
            store=desks,
            execution=execution,
            market_data=market_data,
            raw_response=body.raw_response,
            harness=body.harness,
            model=body.model,
            briefing_fingerprint=body.briefing_fingerprint,
            started_at=body.started_at or datetime.now(UTC),
            harness_error=body.harness_error,
        )
    except DeskError as error:
        raise _desk_http(error) from None
    except DeskStoreUnavailableError as error:
        raise _unavailable(error) from None


@router.get("/{desk_id}/wakes", response_model=tuple[DeskWake, ...])
async def get_wakes(
    desk_id: str,
    desks: Annotated[DeskStore, Depends(get_desk_store)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> tuple[DeskWake, ...]:
    """The newest wakes, newest first."""
    try:
        return await desks.list_wakes(desk_id, limit=limit)
    except DeskStoreUnavailableError as error:
        raise _unavailable(error) from None


@router.get("/{desk_id}/score", response_model=DeskScore)
async def get_score(
    desk_id: str,
    desks: Annotated[DeskStore, Depends(get_desk_store)],
    execution: Annotated[ApiDeskExecution, Depends(_execution)],
    market_data: Annotated[MarketDataService, Depends(get_market_data_service)],
) -> DeskScore:
    """The desk's return since creation beside BTC and equal-weight universe buy-and-hold."""
    try:
        return await score(desk_id, store=desks, execution=execution, market_data=market_data)
    except DeskError as error:
        raise _desk_http(error) from None
    except DeskStoreUnavailableError as error:
        raise _unavailable(error) from None


@router.post("/{desk_id}/status", response_model=Desk)
async def post_status(
    desk_id: str, body: StatusBody, desks: Annotated[DeskStore, Depends(get_desk_store)]
) -> Desk:
    """Pause, resume or retire a desk; its open books keep their stops and targets."""
    try:
        desk = await desks.get_desk(desk_id)
        if desk is None:
            raise HTTPException(status_code=404, detail=f"DESK_NOT_FOUND: no desk {desk_id}.")
        return await save_desk_revision(
            desk.model_copy(update={"status": body.status}), store=desks, previous=desk
        )
    except DeskError as error:
        raise _desk_http(error) from None
    except (DeskStoreUnavailableError, DeskConflictError) as error:
        raise _unavailable(error) from None


def _desk_http(error: DeskError) -> HTTPException:
    """A missing desk is 404; any other desk refusal is 409."""
    code = 404 if str(error).startswith("DESK_NOT_FOUND") else 409
    return HTTPException(status_code=code, detail=str(error))


def _unavailable(error: Exception) -> HTTPException:
    """Storage failures are retryable 503s."""
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error))
