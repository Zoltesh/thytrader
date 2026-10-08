"""JSON boundary for durable fleet operations.

The stored document is validated back into :class:`FleetOperation` before any
caller sees it. A partial or unknown document is a storage failure, not a guess.
"""

from __future__ import annotations

from datetime import datetime
import json
from typing import cast
from uuid import UUID

from thytrader.execution.entry_latch import InhibitionSnapshot
from thytrader.execution.models import ExecutionStoreError
from thytrader.fleet_control.models import (
    FleetAction,
    FleetModeScope,
    FleetOperation,
    FleetOperationStatus,
    FleetTargetStatus,
    TargetResult,
    VenueEffect,
)


def operation_to_json(operation: FleetOperation) -> str:
    """Serialize one operation. The idempotency key is also a table column."""
    payload = {
        "id": str(operation.id),
        "idempotency_key": operation.idempotency_key,
        "action": operation.action.value,
        "mode": operation.mode.value,
        "status": operation.status.value,
        "request_fingerprint": operation.request_fingerprint,
        "created_at": operation.created_at.isoformat(),
        "updated_at": operation.updated_at.isoformat(),
        "live_acknowledged": operation.live_acknowledged,
        "audit_recorded": operation.audit_recorded,
        "note": operation.note,
        "latch_applied": operation.latch_applied,
        "inhibition": _inhibition_payload(operation.inhibition),
        "targets": [_target_payload(item) for item in operation.targets],
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def operation_from_json(text: str) -> FleetOperation:
    """Parse one stored operation or fail closed."""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        raise ExecutionStoreError("Fleet operation record is unreadable.") from error
    if not isinstance(raw, dict):
        raise ExecutionStoreError("Fleet operation record is unreadable.")
    try:
        return FleetOperation(
            id=UUID(str(raw["id"])),
            idempotency_key=_text(raw, "idempotency_key"),
            action=FleetAction(_text(raw, "action")),
            mode=FleetModeScope(_text(raw, "mode")),
            status=FleetOperationStatus(_text(raw, "status")),
            request_fingerprint=_text(raw, "request_fingerprint"),
            created_at=_time(raw, "created_at"),
            updated_at=_time(raw, "updated_at"),
            targets=tuple(_target(item) for item in _list(raw, "targets")),
            inhibition=_inhibition(raw["inhibition"]),
            live_acknowledged=_bool(raw, "live_acknowledged"),
            audit_recorded=_bool(raw, "audit_recorded"),
            note=_text(raw, "note"),
            latch_applied=_bool(raw, "latch_applied"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ExecutionStoreError("Fleet operation record is invalid.") from error


def _inhibition_payload(snapshot: InhibitionSnapshot) -> dict[str, object]:
    """Encode latch fields at the JSON boundary."""
    return {
        "paper_inhibited": snapshot.paper_inhibited,
        "live_inhibited": snapshot.live_inhibited,
        "paper_revision": snapshot.paper_revision,
        "live_revision": snapshot.live_revision,
        "updated_at": None if snapshot.updated_at is None else snapshot.updated_at.isoformat(),
    }


def _target_payload(item: TargetResult) -> dict[str, object]:
    """Encode one book result at the JSON boundary."""
    return {
        "deployment_id": str(item.deployment_id),
        "expected_revision": item.expected_revision,
        "status": item.status.value,
        "detail": item.detail,
        "venue_effect": item.venue_effect.value,
    }


def _mapping(value: object, *, label: str) -> dict[str, object]:
    """Validate one stored JSON object into a string-keyed mapping."""
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def _inhibition(value: object) -> InhibitionSnapshot:
    """Validate one stored latch snapshot."""
    raw = _mapping(value, label="inhibition")
    updated = raw.get("updated_at")
    return InhibitionSnapshot(
        paper_inhibited=_bool(raw, "paper_inhibited"),
        live_inhibited=_bool(raw, "live_inhibited"),
        paper_revision=_int(raw, "paper_revision"),
        live_revision=_int(raw, "live_revision"),
        updated_at=None if updated is None else _parse_time(updated),
    )


def _target(value: object) -> TargetResult:
    """Validate one stored book result."""
    raw = _mapping(value, label="target")
    revision = raw.get("expected_revision")
    return TargetResult(
        deployment_id=UUID(_text(raw, "deployment_id")),
        expected_revision=None if revision is None else _int(raw, "expected_revision"),
        status=FleetTargetStatus(_text(raw, "status")),
        detail=_text(raw, "detail"),
        venue_effect=VenueEffect(_text(raw, "venue_effect")),
    )


def _list(raw: dict[str, object], key: str) -> list[object]:
    """Return one JSON array."""
    value = raw[key]
    if not isinstance(value, list):
        raise TypeError(f"{key} must be a list")
    return cast("list[object]", value)


def _text(raw: dict[str, object], key: str) -> str:
    """Return one JSON string."""
    value = raw[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _bool(raw: dict[str, object], key: str) -> bool:
    """Return one JSON boolean. Integers are not accepted."""
    value = raw[key]
    if not isinstance(value, bool):
        raise TypeError(f"{key} must be a boolean")
    return value


def _int(raw: dict[str, object], key: str) -> int:
    """Return one JSON integer. Booleans are not integers here."""
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer")
    return value


def _time(raw: dict[str, object], key: str) -> datetime:
    """Return one timezone-aware timestamp."""
    return _parse_time(raw[key])


def _parse_time(value: object) -> datetime:
    """Parse an ISO timestamp and refuse a naive value."""
    if not isinstance(value, str):
        raise TypeError("timestamp must be a string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed
