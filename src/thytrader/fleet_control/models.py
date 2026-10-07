"""Typed fleet-control requests, previews, and durable operation results."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID


class FleetAction(StrEnum):
    """Operator-facing fleet action. Disarm never implies stop or flatten."""

    DISARM = "disarm"
    MANAGED_STOP = "managed_stop"
    FLATTEN = "flatten"
    REARM = "rearm"


class FleetModeScope(StrEnum):
    """Mode boundary for one fleet action. ``ALL`` includes live."""

    PAPER = "paper"
    LIVE = "live"
    ALL = "all"


class FleetOperationStatus(StrEnum):
    """Durable progress of one idempotent fleet operation.

    Stop and flatten stay ``accepted`` when every command was recorded, because
    the worker applies venue cancellation or exits later. ``completed`` is only
    for latch changes, which have no venue call.
    """

    PENDING = "pending"
    ACCEPTED = "accepted"
    PARTIAL = "partial"
    COMPLETED = "completed"
    REJECTED = "rejected"


class FleetTargetStatus(StrEnum):
    """One book's outcome inside a fleet operation. Never invents a fill."""

    COMMAND_RECORDED = "command_recorded"
    ALREADY_APPLIED = "already_applied"
    REVISION_CONFLICT = "revision_conflict"
    FAILED = "failed"
    NOT_CONFIRMED = "not_confirmed"
    UNCHANGED = "unchanged"


class VenueEffect(StrEnum):
    """What the recorded command asks the worker to do. Not a fill receipt."""

    NONE = "none"
    ASYNC_MANAGED_SHUTDOWN = "async_managed_shutdown"
    ASYNC_FLATTEN = "async_flatten"


@dataclass(frozen=True, slots=True)
class ResidualPosition:
    """One open book visible at preview time. Absence is unknown, not flat."""

    product_id: str
    side: str
    quantity: str


@dataclass(frozen=True, slots=True)
class FleetTarget:
    """One deployment the operator can confirm, with the revision they saw."""

    deployment_id: UUID
    revision: int
    mode: str
    status: str
    lifecycle_command: str
    product_id: str
    positions: tuple[ResidualPosition, ...] | None
    effect: str


@dataclass(frozen=True, slots=True)
class ExpectedTarget:
    """Revision the operator confirmed. A newer revision is not applied."""

    deployment_id: UUID
    revision: int


@dataclass(frozen=True, slots=True)
class InhibitionSnapshot:
    """Durable per-mode entry latch. Missing modes are not implied clear."""

    paper_inhibited: bool
    live_inhibited: bool
    paper_revision: int
    live_revision: int
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class FleetPreview:
    """Read-only description of what one action would affect."""

    action: FleetAction
    mode: FleetModeScope
    effect: str
    cancels_entries: bool
    flattens: bool
    pauses: bool
    requires_live_acknowledgement: bool
    inhibition: InhibitionSnapshot
    targets: tuple[FleetTarget, ...]
    as_of: datetime


@dataclass(frozen=True, slots=True)
class TargetResult:
    """Per-book result. ``command_recorded`` is not venue completion."""

    deployment_id: UUID
    expected_revision: int | None
    status: FleetTargetStatus
    detail: str
    venue_effect: VenueEffect


@dataclass(frozen=True, slots=True)
class FleetOperation:
    """Idempotent fleet intent and the results recorded so far."""

    id: UUID
    idempotency_key: str
    action: FleetAction
    mode: FleetModeScope
    status: FleetOperationStatus
    request_fingerprint: str
    created_at: datetime
    updated_at: datetime
    targets: tuple[TargetResult, ...]
    inhibition: InhibitionSnapshot
    live_acknowledged: bool
    audit_recorded: bool
    note: str
    latch_applied: bool = False


@dataclass(frozen=True, slots=True)
class ExpectedInhibition:
    """Latch revisions explicitly confirmed from the read-only preview."""

    paper_revision: int | None = None
    live_revision: int | None = None


@dataclass(frozen=True, slots=True)
class FleetExecuteRequest:
    """One confirmed fleet mutation. ``confirm`` must already be true."""

    action: FleetAction
    mode: FleetModeScope
    idempotency_key: str
    expected_targets: tuple[ExpectedTarget, ...]
    live_acknowledged: bool
    allow_empty_scope: bool
    expected_inhibition: ExpectedInhibition = ExpectedInhibition()


InventoryOrder = Literal["created_at_desc_id_desc"]
INVENTORY_ORDER: InventoryOrder = "created_at_desc_id_desc"
SUMMARY_LEDGER_OMISSION = (
    "Historical orders and fills are omitted from this summary. "
    "book_totals and ledger are aggregates, not the historical ledger. "
    "Pass detail=full or read the paged /orders and /fills routes."
)
