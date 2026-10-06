"""Preview and apply fleet disarm, managed stop, flatten, and rearm.

Each book is commanded through the existing deployment service. A failure on
one book is recorded and the walk continues. Nothing here calls a broker, and
a recorded command is not a fill or a flat position.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from thytrader.execution.ids import uuid7
from thytrader.execution.models import (
    DeploymentStatus,
    ExecutionConflictError,
    ExecutionStoreError,
    LifecycleCommand,
)
from thytrader.execution.service import set_deployment_status
from thytrader.fleet_control.admission import remember_snapshot
from thytrader.fleet_control.effects import (
    cancels_entries,
    effect_text,
    flattens,
    pauses,
    requires_live_acknowledgement,
)
from thytrader.fleet_control.models import (
    ExpectedTarget,
    FleetAction,
    FleetExecuteRequest,
    FleetModeScope,
    FleetOperation,
    FleetOperationStatus,
    FleetPreview,
    FleetTarget,
    FleetTargetStatus,
    InhibitionSnapshot,
    ResidualPosition,
    TargetResult,
    VenueEffect,
)
from thytrader.fleet_control.store import fingerprint_request, modes_for, sorted_expected
from thytrader.persistence.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventUnavailableError,
)

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.execution.models import Deployment
    from thytrader.execution.store import ExecutionStore
    from thytrader.fleet_control.store import FleetControlStore
    from thytrader.persistence.audit_events import AuditEventStore

_LIVE_ACK_REQUIRED = (
    "live_acknowledgement_required: This fleet action can re-enable or exit live "
    "trading. Send i_understand_live=true only after the operator explicitly "
    "acknowledged it."
)
_EMPTY_SCOPE = (
    "Fleet action has no confirmed deployment ids. Pass the preview's --expect "
    "values, or --allow-empty-scope after a preview showed no books."
)


async def preview_fleet(
    *,
    execution: ExecutionStore,
    fleet: FleetControlStore,
    action: FleetAction,
    mode: FleetModeScope,
) -> FleetPreview:
    """Describe affected books and the action's effect without mutating."""
    now = datetime.now(UTC)
    inhibition = await fleet.read_inhibition()
    remember_snapshot(
        paper=inhibition.paper_inhibited,
        live=inhibition.live_inhibited,
    )
    rows = await _scoped_rows(execution, mode)
    targets = [await _preview_target(execution, row, action) for row in rows]
    return FleetPreview(
        action=action,
        mode=mode,
        effect=effect_text(action),
        cancels_entries=cancels_entries(action),
        flattens=flattens(action),
        pauses=pauses(action),
        requires_live_acknowledgement=requires_live_acknowledgement(action, mode),
        inhibition=inhibition,
        targets=tuple(targets),
        as_of=now,
    )


async def execute_fleet(
    *,
    execution: ExecutionStore,
    fleet: FleetControlStore,
    request: FleetExecuteRequest,
    audit: AuditEventStore | None = None,
) -> FleetOperation:
    """Apply one confirmed fleet action, or return the durable result for its key."""
    _require_request(request)
    fingerprint = _fingerprint(request)
    async with fleet.operation_guard(request.idempotency_key):
        existing = await fleet.get_operation(request.idempotency_key)
        if existing is not None:
            _require_same_fingerprint(existing, fingerprint)
            if existing.status is not FleetOperationStatus.PENDING:
                return existing
            operation = existing
        else:
            operation = await _insert_pending(fleet, request, fingerprint)
        return await _apply_pending(
            execution=execution,
            fleet=fleet,
            operation=operation,
            request=request,
            audit=audit,
        )


async def _apply_pending(
    *,
    execution: ExecutionStore,
    fleet: FleetControlStore,
    operation: FleetOperation,
    request: FleetExecuteRequest,
    audit: AuditEventStore | None,
) -> FleetOperation:
    """Continue a pending operation without repeating recorded targets."""
    inhibition = await _mutate_latch(fleet, request)
    recorded = {item.deployment_id: item for item in operation.targets}
    results = list(operation.targets)
    if request.action in {FleetAction.MANAGED_STOP, FleetAction.FLATTEN}:
        results = await _apply_books(
            execution=execution,
            request=request,
            recorded=recorded,
            fleet=fleet,
            operation=operation,
        )
    note = _completion_note(request.action)
    status = _status_for(request.action, results)
    finished = replace(
        operation,
        status=status,
        targets=tuple(results),
        inhibition=inhibition,
        updated_at=datetime.now(UTC),
        note=note,
        audit_recorded=False,
    )
    finished = await _audit(audit, finished)
    await fleet.save_operation(finished)
    return finished


async def _apply_books(
    *,
    execution: ExecutionStore,
    request: FleetExecuteRequest,
    recorded: dict[UUID, TargetResult],
    fleet: FleetControlStore,
    operation: FleetOperation,
) -> list[TargetResult]:
    """Record each confirmed command, persisting after every book."""
    results = [item for item in operation.targets if item.deployment_id in recorded]
    confirmed = {item.deployment_id: item for item in request.expected_targets}
    current = {row.id: row for row in await _scoped_rows(execution, request.mode)}
    for expected in request.expected_targets:
        if expected.deployment_id in recorded:
            continue
        outcome = await _one_target(
            execution, expected, current.get(expected.deployment_id), request
        )
        results.append(outcome)
        operation = replace(
            operation,
            targets=tuple(results),
            updated_at=datetime.now(UTC),
        )
        await fleet.save_operation(operation)
    for deployment_id, row in current.items():
        if deployment_id in confirmed or deployment_id in {item.deployment_id for item in results}:
            continue
        results.append(_not_confirmed(row))
    return results


async def _one_target(
    execution: ExecutionStore,
    expected: ExpectedTarget,
    row: Deployment | None,
    request: FleetExecuteRequest,
) -> TargetResult:
    """Apply one confirmed revision, or record why it was not applied."""
    if row is None:
        return TargetResult(
            expected.deployment_id,
            expected.revision,
            FleetTargetStatus.FAILED,
            "Deployment is not in the confirmed mode scope.",
            VenueEffect.NONE,
        )
    if row.revision != expected.revision:
        return TargetResult(
            expected.deployment_id,
            expected.revision,
            FleetTargetStatus.REVISION_CONFLICT,
            f"Revision is {row.revision}, not the confirmed {expected.revision}.",
            VenueEffect.NONE,
        )
    if _already_applied(row, request.action):
        return TargetResult(
            expected.deployment_id,
            expected.revision,
            FleetTargetStatus.ALREADY_APPLIED,
            "The requested lifecycle command is already recorded.",
            _venue_effect(request.action),
        )
    return await _record_command(execution, row, request.action)


async def _record_command(
    execution: ExecutionStore,
    row: Deployment,
    action: FleetAction,
) -> TargetResult:
    """Persist one existing lifecycle command. Venue work stays with the worker."""
    try:
        await set_deployment_status(
            store=execution,
            deployment_id=row.id,
            status=DeploymentStatus.STOPPED,
            flatten=action is FleetAction.FLATTEN,
        )
    except ExecutionConflictError as error:
        return TargetResult(
            row.id,
            row.revision,
            FleetTargetStatus.REVISION_CONFLICT,
            str(error),
            VenueEffect.NONE,
        )
    except ExecutionStoreError as error:
        return TargetResult(
            row.id,
            row.revision,
            FleetTargetStatus.FAILED,
            str(error),
            VenueEffect.NONE,
        )
    return TargetResult(
        row.id,
        row.revision,
        FleetTargetStatus.COMMAND_RECORDED,
        "Lifecycle command recorded. Worker completion is asynchronous and not a fill.",
        _venue_effect(action),
    )


async def _mutate_latch(
    fleet: FleetControlStore, request: FleetExecuteRequest
) -> InhibitionSnapshot:
    """Change the latch only for disarm and rearm. Stop and flatten do not."""
    now = datetime.now(UTC)
    modes = modes_for(request.mode.value)
    if request.action is FleetAction.DISARM:
        snapshot = await fleet.inhibit(modes, now=now)
    elif request.action is FleetAction.REARM:
        snapshot = await fleet.release(modes, now=now)
    else:
        snapshot = await fleet.read_inhibition()
    remember_snapshot(paper=snapshot.paper_inhibited, live=snapshot.live_inhibited)
    return snapshot


async def _preview_target(
    execution: ExecutionStore, row: Deployment, action: FleetAction
) -> FleetTarget:
    """Load residual positions without substituting an empty tuple for a failure."""
    positions: tuple[ResidualPosition, ...] | None
    try:
        summary = await execution.get_deployment_summary(row.id)
    except ExecutionStoreError:
        positions = None
    else:
        positions = tuple(
            ResidualPosition(
                product_id=item.product_id or row.product_id,
                side=item.side.value,
                quantity=format(item.quantity, "f"),
            )
            for item in summary.positions
        )
    return FleetTarget(
        deployment_id=row.id,
        revision=row.revision,
        mode=row.mode.value,
        status=row.status.value,
        lifecycle_command=row.lifecycle_command.value,
        product_id=row.product_id,
        positions=positions,
        effect=_target_effect(action, row),
    )


def _target_effect(action: FleetAction, row: Deployment) -> str:
    """Per-book effect, including books the action will not change."""
    if action is FleetAction.DISARM:
        return "Entries inhibited. This book is not paused, cancelled, or flattened."
    if action is FleetAction.REARM:
        return "Latch clear does not resume this book."
    if row.status is DeploymentStatus.STOPPED and _already_applied(row, action):
        return "Already recorded. No additional venue command is implied."
    if action is FleetAction.FLATTEN:
        return "Explicit flatten will be recorded. Not a pause."
    return "Managed shutdown will be recorded. Protection stays. Not a flatten."


def _already_applied(row: Deployment, action: FleetAction) -> bool:
    """True when the requested command is already the stored lifecycle command."""
    if row.status is not DeploymentStatus.STOPPED:
        return False
    if action is FleetAction.FLATTEN:
        return row.lifecycle_command is LifecycleCommand.FLATTEN
    return row.lifecycle_command is LifecycleCommand.MANAGED_SHUTDOWN


def _venue_effect(action: FleetAction) -> VenueEffect:
    """Map an action onto the asynchronous worker effect label."""
    if action is FleetAction.FLATTEN:
        return VenueEffect.ASYNC_FLATTEN
    if action is FleetAction.MANAGED_STOP:
        return VenueEffect.ASYNC_MANAGED_SHUTDOWN
    return VenueEffect.NONE


def _not_confirmed(row: Deployment) -> TargetResult:
    """Report a scoped book the operator did not confirm. It is not commanded."""
    return TargetResult(
        row.id,
        None,
        FleetTargetStatus.NOT_CONFIRMED,
        "In scope but not in the confirmed id list. Not commanded.",
        VenueEffect.NONE,
    )


def _status_for(action: FleetAction, results: list[TargetResult]) -> FleetOperationStatus:
    """Summarize book results. Latch-only actions complete without venue work."""
    if action in {FleetAction.DISARM, FleetAction.REARM}:
        return FleetOperationStatus.COMPLETED
    if any(
        item.status in {FleetTargetStatus.FAILED, FleetTargetStatus.REVISION_CONFLICT}
        for item in results
    ):
        return FleetOperationStatus.PARTIAL
    if any(item.status is FleetTargetStatus.NOT_CONFIRMED for item in results):
        return FleetOperationStatus.PARTIAL
    return FleetOperationStatus.ACCEPTED


def _completion_note(action: FleetAction) -> str:
    """State the async boundary so a caller cannot treat acceptance as a fill."""
    if action in {FleetAction.DISARM, FleetAction.REARM}:
        return "Latch updated in durable state. No deployment lifecycle command was recorded."
    return (
        "Commands were recorded per book where status is command_recorded. "
        "This is not an atomic venue transaction and does not prove orders "
        "cancelled or positions flat."
    )


def _require_request(request: FleetExecuteRequest) -> None:
    """Refuse missing confirmation, missing live ack, or an unconfirmed stop scope."""
    if (
        requires_live_acknowledgement(request.action, request.mode)
        and not request.live_acknowledged
    ):
        raise ExecutionConflictError(_LIVE_ACK_REQUIRED)
    needs_ids = request.action in {FleetAction.MANAGED_STOP, FleetAction.FLATTEN}
    if needs_ids and not request.expected_targets and not request.allow_empty_scope:
        raise ExecutionConflictError(_EMPTY_SCOPE)


def _fingerprint(request: FleetExecuteRequest) -> str:
    """Build the retry identity for one confirmed request."""
    pairs = tuple((str(item.deployment_id), item.revision) for item in request.expected_targets)
    return fingerprint_request(
        action=request.action.value,
        mode=request.mode.value,
        expected=sorted_expected(pairs),
        live_acknowledged=request.live_acknowledged,
        allow_empty_scope=request.allow_empty_scope,
    )


def _require_same_fingerprint(existing: FleetOperation, fingerprint: str) -> None:
    """Refuse to replay a key for a different action, mode, or id list."""
    if existing.request_fingerprint != fingerprint:
        raise ExecutionConflictError(
            "idempotency_key already used for a different fleet request. "
            "Repeat the same action, mode, acknowledgement, and expected revisions."
        )


async def _insert_pending(
    fleet: FleetControlStore,
    request: FleetExecuteRequest,
    fingerprint: str,
) -> FleetOperation:
    """Persist the intent before applying any book command."""
    now = datetime.now(UTC)
    inhibition = await fleet.read_inhibition()
    operation = FleetOperation(
        id=uuid7(now),
        idempotency_key=request.idempotency_key,
        action=request.action,
        mode=request.mode,
        status=FleetOperationStatus.PENDING,
        request_fingerprint=fingerprint,
        created_at=now,
        updated_at=now,
        targets=(),
        inhibition=inhibition,
        live_acknowledged=request.live_acknowledged,
        audit_recorded=False,
        note="Intent recorded. Book commands have not been applied.",
    )
    inserted = await fleet.insert_operation(operation)
    if inserted:
        return operation
    existing = await fleet.get_operation(request.idempotency_key)
    if existing is None:
        raise ExecutionStoreError("Fleet operation storage conflict.")
    _require_same_fingerprint(existing, fingerprint)
    return existing


async def _scoped_rows(execution: ExecutionStore, mode: FleetModeScope) -> tuple[Deployment, ...]:
    """Load every deployment in scope, not a silent 50-row page."""
    rows = await execution.list_deployments()
    selected = [row for row in rows if mode is FleetModeScope.ALL or row.mode.value == mode.value]
    selected.sort(key=lambda row: (row.created_at, str(row.id)), reverse=True)
    return tuple(selected)


async def _audit(audit: AuditEventStore | None, operation: FleetOperation) -> FleetOperation:
    """Append one redacted runtime audit event. Audit failure does not undo the command."""
    if audit is None:
        return operation
    identifiers = ",".join(str(item.deployment_id) for item in operation.targets[:20])
    event = AuditEvent(
        occurred_at=operation.updated_at,
        category=AuditEventCategory.RUNTIME,
        action=f"fleet_{operation.action.value}",
        outcome=_audit_outcome(operation.status),
        detail=(
            f"operation_id={operation.id} mode={operation.mode.value} "
            f"status={operation.status.value} target_count={len(operation.targets)} "
            f"deployment_ids={identifiers}"
        ),
    )
    try:
        await audit.append(event)
    except AuditEventUnavailableError:
        return replace(operation, audit_recorded=False)
    return replace(operation, audit_recorded=True)


def _audit_outcome(status: FleetOperationStatus) -> AuditEventOutcome:
    """Map a fleet status onto the existing audit outcome enum."""
    if status is FleetOperationStatus.PARTIAL:
        return AuditEventOutcome.FAILURE
    return AuditEventOutcome.SUCCESS
