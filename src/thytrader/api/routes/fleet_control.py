"""Confirmation-gated fleet disarm, managed stop, flatten, and rearm.

GET preview does not mutate. POST requires the application trust boundary,
``confirm=true``, and a live acknowledgement when the action can re-enable or
exit live books. Disarm never records a flatten.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from thytrader.api.dependencies import (
    get_audit_event_store,
    get_execution_store,
    get_fleet_control_store,
)
from thytrader.execution.models import ExecutionConflictError, ExecutionStoreError
from thytrader.execution.store import ExecutionStore
from thytrader.fleet_control.models import (
    ExpectedTarget,
    FleetAction,
    FleetExecuteRequest,
    FleetModeScope,
    FleetOperation,
    FleetPreview,
    FleetTarget,
    InhibitionSnapshot,
)
from thytrader.fleet_control.service import execute_fleet, preview_fleet
from thytrader.fleet_control.store import FleetControlStore
from thytrader.persistence.audit_events import AuditEventStore

router = APIRouter(prefix="/api/v1/fleet-control", tags=["fleet-control"])


class FleetInhibitionResponse(BaseModel):
    """Durable per-mode entry latch."""

    model_config = ConfigDict(extra="forbid")
    paper_inhibited: bool
    live_inhibited: bool
    paper_revision: int
    live_revision: int
    updated_at: str | None


class ResidualPositionResponse(BaseModel):
    """One open book. A missing positions array means the read failed."""

    model_config = ConfigDict(extra="forbid")
    product_id: str
    side: str
    quantity: str


class FleetTargetResponse(BaseModel):
    """One previewed book and the effect that would apply to it."""

    model_config = ConfigDict(extra="forbid")
    deployment_id: UUID
    revision: int
    mode: str
    status: str
    lifecycle_command: str
    product_id: str
    positions: tuple[ResidualPositionResponse, ...] | None
    positions_known: bool
    effect: str


class FleetPreviewResponse(BaseModel):
    """Read-only fleet preview. ``flattens`` is true only for explicit flatten."""

    model_config = ConfigDict(extra="forbid")
    action: str
    mode: str
    effect: str
    cancels_entries: bool
    flattens: bool
    pauses: bool
    requires_live_acknowledgement: bool
    inhibition: FleetInhibitionResponse
    targets: tuple[FleetTargetResponse, ...]
    as_of: str


class ExpectedTargetBody(BaseModel):
    """One confirmed deployment revision."""

    model_config = ConfigDict(extra="forbid")
    deployment_id: UUID
    revision: int = Field(ge=0)


class FleetExecuteBody(BaseModel):
    """Confirmed fleet mutation. Extra fields are rejected."""

    model_config = ConfigDict(extra="forbid")
    mode: Literal["paper", "live", "all"]
    confirm: StrictBool
    idempotency_key: str = Field(min_length=1, max_length=128)
    i_understand_live: StrictBool = False
    expected_targets: tuple[ExpectedTargetBody, ...] = ()
    allow_empty_scope: StrictBool = False


class TargetResultResponse(BaseModel):
    """One book result. ``command_recorded`` is not venue completion."""

    model_config = ConfigDict(extra="forbid")
    deployment_id: UUID
    expected_revision: int | None
    status: str
    detail: str
    venue_effect: str


class FleetOperationResponse(BaseModel):
    """Durable fleet result. Venue effects are asynchronous and not atomic."""

    model_config = ConfigDict(extra="forbid")
    id: UUID
    idempotency_key: str
    action: str
    mode: str
    status: str
    targets: tuple[TargetResultResponse, ...]
    inhibition: FleetInhibitionResponse
    live_acknowledged: bool
    audit_recorded: bool
    note: str
    atomic_venue_transaction: bool = False


@router.get("/preview", response_model=FleetPreviewResponse)
async def get_fleet_preview(
    execution: Annotated[ExecutionStore, Depends(get_execution_store)],
    fleet: Annotated[FleetControlStore, Depends(get_fleet_control_store)],
    action: Annotated[Literal["disarm", "managed_stop", "flatten", "rearm"], Query()],
    mode: Annotated[Literal["paper", "live", "all"], Query()],
) -> FleetPreviewResponse:
    """Preview affected ids, revisions, and residual positions without mutating."""
    try:
        preview = await preview_fleet(
            execution=execution,
            fleet=fleet,
            action=FleetAction(action),
            mode=FleetModeScope(mode),
        )
    except ExecutionStoreError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from None
    return _preview_response(preview)


@router.get("", response_model=FleetInhibitionResponse)
async def get_fleet_inhibition(
    fleet: Annotated[FleetControlStore, Depends(get_fleet_control_store)],
) -> FleetInhibitionResponse:
    """Read the durable entry latch. This does not list or change deployments."""
    try:
        snapshot = await fleet.read_inhibition()
    except ExecutionStoreError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from None
    return _inhibition_response(snapshot)


@router.post("/disarm", response_model=FleetOperationResponse)
async def post_fleet_disarm(
    body: FleetExecuteBody,
    execution: Annotated[ExecutionStore, Depends(get_execution_store)],
    fleet: Annotated[FleetControlStore, Depends(get_fleet_control_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
) -> FleetOperationResponse:
    """Inhibit entries. This route cannot flatten, even if a client sends that intent."""
    return await _execute(FleetAction.DISARM, body, execution, fleet, audit)


@router.post("/stop", response_model=FleetOperationResponse)
async def post_fleet_stop(
    body: FleetExecuteBody,
    execution: Annotated[ExecutionStore, Depends(get_execution_store)],
    fleet: Annotated[FleetControlStore, Depends(get_fleet_control_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
) -> FleetOperationResponse:
    """Record managed shutdown for the confirmed ids. Not a flatten."""
    return await _execute(FleetAction.MANAGED_STOP, body, execution, fleet, audit)


@router.post("/flatten", response_model=FleetOperationResponse)
async def post_fleet_flatten(
    body: FleetExecuteBody,
    execution: Annotated[ExecutionStore, Depends(get_execution_store)],
    fleet: Annotated[FleetControlStore, Depends(get_fleet_control_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
) -> FleetOperationResponse:
    """Record explicit flatten for the confirmed ids. Live scope needs the live ack."""
    return await _execute(FleetAction.FLATTEN, body, execution, fleet, audit)


@router.post("/rearm", response_model=FleetOperationResponse)
async def post_fleet_rearm(
    body: FleetExecuteBody,
    execution: Annotated[ExecutionStore, Depends(get_execution_store)],
    fleet: Annotated[FleetControlStore, Depends(get_fleet_control_store)],
    audit: Annotated[AuditEventStore, Depends(get_audit_event_store)],
) -> FleetOperationResponse:
    """Clear entry inhibition. Does not resume books. Live scope needs the live ack."""
    return await _execute(FleetAction.REARM, body, execution, fleet, audit)


async def _execute(
    action: FleetAction,
    body: FleetExecuteBody,
    execution: ExecutionStore,
    fleet: FleetControlStore,
    audit: AuditEventStore,
) -> FleetOperationResponse:
    """Run one confirmed action and map conflicts to HTTP."""
    if body.confirm is not True:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="confirmation_required: Fleet mutations require confirm=true.",
        )
    request = FleetExecuteRequest(
        action=action,
        mode=FleetModeScope(body.mode),
        idempotency_key=body.idempotency_key,
        expected_targets=tuple(
            ExpectedTarget(item.deployment_id, item.revision) for item in body.expected_targets
        ),
        live_acknowledged=body.i_understand_live,
        allow_empty_scope=body.allow_empty_scope,
    )
    try:
        operation = await execute_fleet(
            execution=execution, fleet=fleet, request=request, audit=audit
        )
    except ExecutionConflictError as error:
        code = (
            status.HTTP_428_PRECONDITION_REQUIRED
            if str(error).startswith("live_acknowledgement_required:")
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=str(error)) from None
    except ExecutionStoreError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from None
    return _operation_response(operation)


def _preview_response(preview: FleetPreview) -> FleetPreviewResponse:
    """Serialize a preview without implying that positions were changed."""
    return FleetPreviewResponse(
        action=preview.action.value,
        mode=preview.mode.value,
        effect=preview.effect,
        cancels_entries=preview.cancels_entries,
        flattens=preview.flattens,
        pauses=preview.pauses,
        requires_live_acknowledgement=preview.requires_live_acknowledgement,
        inhibition=_inhibition_response(preview.inhibition),
        targets=tuple(_target_response(item) for item in preview.targets),
        as_of=preview.as_of.isoformat(),
    )


def _target_response(item: FleetTarget) -> FleetTargetResponse:
    """Serialize one preview target, preserving an unknown position read."""
    positions = (
        None
        if item.positions is None
        else tuple(
            ResidualPositionResponse(
                product_id=position.product_id,
                side=position.side,
                quantity=position.quantity,
            )
            for position in item.positions
        )
    )
    return FleetTargetResponse(
        deployment_id=item.deployment_id,
        revision=item.revision,
        mode=item.mode,
        status=item.status,
        lifecycle_command=item.lifecycle_command,
        product_id=item.product_id,
        positions=positions,
        positions_known=item.positions is not None,
        effect=item.effect,
    )


def _operation_response(operation: FleetOperation) -> FleetOperationResponse:
    """Serialize a durable result, including partial and async notes."""
    return FleetOperationResponse(
        id=operation.id,
        idempotency_key=operation.idempotency_key,
        action=operation.action.value,
        mode=operation.mode.value,
        status=operation.status.value,
        targets=tuple(
            TargetResultResponse(
                deployment_id=item.deployment_id,
                expected_revision=item.expected_revision,
                status=item.status.value,
                detail=item.detail,
                venue_effect=item.venue_effect.value,
            )
            for item in operation.targets
        ),
        inhibition=_inhibition_response(operation.inhibition),
        live_acknowledged=operation.live_acknowledged,
        audit_recorded=operation.audit_recorded,
        note=operation.note,
    )


def _inhibition_response(snapshot: InhibitionSnapshot) -> FleetInhibitionResponse:
    """Serialize the latch without treating a missing timestamp as now."""
    return FleetInhibitionResponse(
        paper_inhibited=snapshot.paper_inhibited,
        live_inhibited=snapshot.live_inhibited,
        paper_revision=snapshot.paper_revision,
        live_revision=snapshot.live_revision,
        updated_at=None if snapshot.updated_at is None else snapshot.updated_at.isoformat(),
    )
