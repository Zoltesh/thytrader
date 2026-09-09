"""Paper and live deployment HTTP contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID  # noqa: TC003 - FastAPI resolves this annotation at runtime.

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from thytrader.api.dependencies import (
    get_audit_event_store,
    get_execution_store,
    get_runtime_state,
    get_strategy_publication_store,
)
from thytrader.execution.models import (
    Deployment,
    DeploymentMode,
    DeploymentSnapshot,
    DeploymentStatus,
    ExecutionConflictError,
    ExecutionStoreError,
    Fill,
    Order,
    Position,
)
from thytrader.execution.service import create_deployment, parse_decimal, set_deployment_status
from thytrader.execution.store import ExecutionStore  # noqa: TC001 - FastAPI Depends.
from thytrader.persistence.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventStore,
)
from thytrader.runtime import RuntimeState  # noqa: TC001 - FastAPI Depends.
from thytrader.strategies.publication import StrategyPublicationStore  # noqa: TC001

router = APIRouter(prefix="/api/v1/deployments", tags=["deployments"])


class CreateDeploymentRequest(BaseModel):
    """Start one paper or live runtime for an immutable published strategy."""

    strategy_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    mode: DeploymentMode
    paper_starting_cash: str | None = None


class PositionResponse(BaseModel):
    """The single long position, when the deployment is in the market."""

    quantity: str
    entry_price: str
    stop_price: str
    target_price: str
    entered_bar: str


class OrderResponse(BaseModel):
    """One persisted venue-visible order."""

    id: UUID
    client_order_id: str
    venue_order_id: str | None
    side: str
    kind: str
    quantity: str
    price: str | None
    filled_quantity: str
    status: str
    reject_reason: str | None
    created_at: str
    updated_at: str


class FillResponse(BaseModel):
    """One persisted fill."""

    id: UUID
    order_id: UUID
    venue_fill_id: str
    price: str
    quantity: str
    fee: str
    filled_at: str


class DeploymentResponse(BaseModel):
    """One deployment plus optional runtime evidence."""

    id: UUID
    strategy_fingerprint: str
    strategy_id: UUID
    product_id: str
    mode: str
    status: str
    phase: str
    cash: str
    paper_starting_cash: str | None
    last_evaluated_bar: str | None
    last_signal: str | None
    mismatch_detail: str | None
    pending_entry_bars: int
    bars_held: int
    created_at: str
    updated_at: str
    position: PositionResponse | None = None
    orders: tuple[OrderResponse, ...] = ()
    fills: tuple[FillResponse, ...] = ()


class DeploymentListResponse(BaseModel):
    """Newest-first deployments."""

    deployments: tuple[DeploymentResponse, ...]


@router.post("", response_model=DeploymentResponse, status_code=status.HTTP_201_CREATED)
async def post_deployment(
    body: CreateDeploymentRequest,
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
    publication_store: Annotated[StrategyPublicationStore, Depends(get_strategy_publication_store)],
    runtime: Annotated[RuntimeState, Depends(get_runtime_state)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
) -> DeploymentResponse:
    """Create a running paper or live deployment without waiting for the worker."""
    try:
        deployment = await create_deployment(
            store=store,
            publication_store=publication_store,
            strategy_fingerprint=body.strategy_fingerprint,
            mode=body.mode,
            paper_starting_cash=parse_decimal(body.paper_starting_cash),
            live_allowed=runtime.settings.coinbase_api_key_name is not None,
        )
    except ExecutionConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from None
    except ExecutionStoreError as error:
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in str(error).lower()
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(status_code=code, detail=str(error)) from None
    await _append_runtime_audit(
        audit,
        action="start_live" if deployment.mode is DeploymentMode.LIVE else "start_paper",
        deployment=deployment,
    )
    return _deployment_response(deployment)


@router.get("", response_model=DeploymentListResponse)
async def list_deployments(
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
) -> DeploymentListResponse:
    """Return every deployment, newest-updated first."""
    try:
        deployments = await store.list_deployments()
        snapshots = [await store.get_deployment(item.id) for item in deployments]
    except ExecutionStoreError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from None
    return DeploymentListResponse(deployments=tuple(_snapshot_response(item) for item in snapshots))


@router.get("/{deployment_id}", response_model=DeploymentResponse)
async def get_deployment(
    deployment_id: UUID,
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
) -> DeploymentResponse:
    """Return one deployment with orders, fills, and position."""
    snapshot = await _require_snapshot(store, deployment_id)
    return _snapshot_response(snapshot)


@router.post("/{deployment_id}/pause", response_model=DeploymentResponse)
async def pause_deployment(
    deployment_id: UUID,
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
) -> DeploymentResponse:
    """Pause a running deployment so the worker skips new orders."""
    return await _set_status(store, audit, deployment_id, DeploymentStatus.PAUSED)


@router.post("/{deployment_id}/resume", response_model=DeploymentResponse)
async def resume_deployment(
    deployment_id: UUID,
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
) -> DeploymentResponse:
    """Resume a paused deployment."""
    return await _set_status(store, audit, deployment_id, DeploymentStatus.RUNNING)


@router.post("/{deployment_id}/stop", response_model=DeploymentResponse)
async def stop_deployment(
    deployment_id: UUID,
    store: Annotated[ExecutionStore, Depends(get_execution_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
) -> DeploymentResponse:
    """Stop a deployment permanently."""
    return await _set_status(store, audit, deployment_id, DeploymentStatus.STOPPED)


async def _set_status(
    store: ExecutionStore,
    audit: AuditEventStore,
    deployment_id: UUID,
    status_value: DeploymentStatus,
) -> DeploymentResponse:
    """Apply one status change and return the resulting snapshot."""
    try:
        snapshot = await set_deployment_status(
            store=store, deployment_id=deployment_id, status=status_value
        )
    except ExecutionConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from None
    except ExecutionStoreError as error:
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in str(error).lower()
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(status_code=code, detail=str(error)) from None
    await _append_runtime_audit(
        audit,
        action=_status_action(status_value),
        deployment=snapshot.deployment,
    )
    return _snapshot_response(snapshot)


def _status_action(status_value: DeploymentStatus) -> str:
    """Map a status change onto a stable audit action name."""
    if status_value is DeploymentStatus.PAUSED:
        return "pause_deployment"
    if status_value is DeploymentStatus.STOPPED:
        return "stop_deployment"
    return "resume_deployment"


async def _append_runtime_audit(
    audit: AuditEventStore,
    *,
    action: str,
    deployment: Deployment,
) -> None:
    """Record one runtime control action without cash, quantities, or secrets."""
    event = AuditEvent(
        occurred_at=datetime.now(UTC),
        category=AuditEventCategory.RUNTIME,
        action=action,
        outcome=AuditEventOutcome.SUCCESS,
        detail=(
            f"deployment_id={deployment.id} mode={deployment.mode.value} "
            f"status={deployment.status.value} fingerprint={deployment.strategy_fingerprint}"
        ),
        product_id=deployment.product_id,
    )
    await audit.append(event)


async def _require_snapshot(store: ExecutionStore, deployment_id: UUID) -> DeploymentSnapshot:
    """Load one snapshot or map storage errors into HTTP failures."""
    try:
        return await store.get_deployment(deployment_id)
    except ExecutionStoreError as error:
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in str(error).lower()
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(status_code=code, detail=str(error)) from None


def _deployment_response(deployment: Deployment) -> DeploymentResponse:
    """Serialize one deployment without related collections."""
    return DeploymentResponse(
        id=deployment.id,
        strategy_fingerprint=deployment.strategy_fingerprint,
        strategy_id=deployment.strategy_id,
        product_id=deployment.product_id,
        mode=deployment.mode.value,
        status=deployment.status.value,
        phase=deployment.phase.value,
        cash=format(deployment.cash, "f"),
        paper_starting_cash=(
            None
            if deployment.paper_starting_cash is None
            else format(deployment.paper_starting_cash, "f")
        ),
        last_evaluated_bar=(
            None
            if deployment.last_evaluated_bar is None
            else deployment.last_evaluated_bar.isoformat()
        ),
        last_signal=deployment.last_signal,
        mismatch_detail=deployment.mismatch_detail,
        pending_entry_bars=deployment.pending_entry_bars,
        bars_held=deployment.bars_held,
        created_at=deployment.created_at.isoformat(),
        updated_at=deployment.updated_at.isoformat(),
    )


def _snapshot_response(snapshot: DeploymentSnapshot) -> DeploymentResponse:
    """Serialize one deployment together with position, orders, and fills."""
    response = _deployment_response(snapshot.deployment)
    return response.model_copy(
        update={
            "position": (
                None if snapshot.position is None else _position_response(snapshot.position)
            ),
            "orders": tuple(_order_response(order) for order in snapshot.orders),
            "fills": tuple(_fill_response(fill) for fill in snapshot.fills),
        }
    )


def _position_response(position: Position) -> PositionResponse:
    """Serialize one open long position."""
    return PositionResponse(
        quantity=format(position.quantity, "f"),
        entry_price=format(position.entry_price, "f"),
        stop_price=format(position.stop_price, "f"),
        target_price=format(position.target_price, "f"),
        entered_bar=position.entered_bar.isoformat(),
    )


def _order_response(order: Order) -> OrderResponse:
    """Serialize one order snapshot."""
    return OrderResponse(
        id=order.id,
        client_order_id=order.client_order_id,
        venue_order_id=order.venue_order_id,
        side=order.side.value,
        kind=order.kind.value,
        quantity=format(order.quantity, "f"),
        price=None if order.price is None else format(order.price, "f"),
        filled_quantity=format(order.filled_quantity, "f"),
        status=order.status.value,
        reject_reason=order.reject_reason,
        created_at=order.created_at.isoformat(),
        updated_at=order.updated_at.isoformat(),
    )


def _fill_response(fill: Fill) -> FillResponse:
    """Serialize one fill."""
    return FillResponse(
        id=fill.id,
        order_id=fill.order_id,
        venue_fill_id=fill.venue_fill_id,
        price=format(fill.price, "f"),
        quantity=format(fill.quantity, "f"),
        fee=format(fill.fee, "f"),
        filled_at=fill.filled_at.isoformat(),
    )
