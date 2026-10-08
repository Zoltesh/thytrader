"""Preview and apply fleet disarm, managed stop, flatten, and rearm.

Each book is commanded through the existing deployment service. A failure on
one book is recorded and the walk continues. Nothing here calls a broker, and
a recorded command is not a fill or a flat position.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from thytrader.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventUnavailableError,
)
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
    ResidualPosition,
    TargetResult,
    VenueEffect,
)
from thytrader.fleet_control.store import fingerprint_request, modes_for, sorted_expected
from thytrader.trading.ids import uuid7
from thytrader.trading.models import (
    DeploymentStatus,
    ExecutionConflictError,
    ExecutionStoreError,
    LifecycleCommand,
)

if TYPE_CHECKING:
    from thytrader.audit_events import AuditEventStore
    from thytrader.fleet_control.store import FleetControlStore
    from thytrader.trading.models import Deployment
    from thytrader.trading.store import ExecutionStore

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
    fleet.validate_execution(execution)
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
    if request.action in {FleetAction.DISARM, FleetAction.REARM}:
        operation = await fleet.apply_latch(operation, request, now=datetime.now(UTC))
    else:
        operation = await _apply_books(
            execution=execution,
            request=request,
            fleet=fleet,
            operation=operation,
        )
    inhibition = operation.inhibition
    results = list(operation.targets)
    current_inhibition = await fleet.read_inhibition()
    remember_snapshot(
        paper=current_inhibition.paper_inhibited, live=current_inhibition.live_inhibited
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
    fleet: FleetControlStore,
    operation: FleetOperation,
) -> FleetOperation:
    """Commit each confirmed command with its receipt; replay uses receipts only."""
    recorded = {item.deployment_id for item in operation.targets}
    confirmed = {item.deployment_id for item in request.expected_targets}
    for expected in request.expected_targets:
        if expected.deployment_id in recorded:
            continue
        operation = await _record_target(execution, fleet, operation, expected)
    current = await _scoped_rows(execution, request.mode)
    omitted = tuple(
        _not_confirmed(row) for row in current if row.id not in confirmed and row.id not in recorded
    )
    return replace(operation, targets=(*operation.targets, *omitted))


async def _record_target(
    execution: ExecutionStore,
    fleet: FleetControlStore,
    operation: FleetOperation,
    expected: ExpectedTarget,
) -> FleetOperation:
    """Record only known outcomes; unknown exceptions leave progress pending."""
    try:
        return await fleet.record_target(execution, operation, expected, now=datetime.now(UTC))
    except ExecutionConflictError as error:
        outcome = FleetTargetStatus.REVISION_CONFLICT
        detail = str(error)
    except ExecutionStoreError as error:
        outcome = FleetTargetStatus.FAILED
        detail = str(error)
    durable = await fleet.get_operation(operation.idempotency_key)
    if durable is None:
        raise ExecutionStoreError("Fleet receipt is unavailable; result remains unknown.")
    if any(item.deployment_id == expected.deployment_id for item in durable.targets):
        return durable
    result = TargetResult(
        expected.deployment_id, expected.revision, outcome, detail, VenueEffect.NONE
    )
    saved = replace(durable, targets=(*durable.targets, result), updated_at=datetime.now(UTC))
    await fleet.save_operation(saved)
    return saved


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
    if len({item.deployment_id for item in request.expected_targets}) != len(
        request.expected_targets
    ):
        raise ExecutionConflictError("Duplicate confirmed deployment ids are not allowed.")
    if request.action in {FleetAction.DISARM, FleetAction.REARM}:
        expected = request.expected_inhibition
        confirmed = {"paper": expected.paper_revision, "live": expected.live_revision}
        if any(confirmed[mode] is None for mode in modes_for(request.mode.value)):
            raise ExecutionConflictError(
                "expected_inhibition_required: Confirm preview latch revisions."
            )
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
        inhibition=request.expected_inhibition,
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
