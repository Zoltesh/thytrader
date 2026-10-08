"""Protection status vocabulary and evidence values for one product book.

Status, mechanism and position-state enums, the stable reason codes in display order,
the evidence value the classifier returns and its strict HTTP response model
(ADR 0097, ADR 0098, ADR 0112).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from thytrader.trading.models import (
    IntentPurpose,
    OrderSide,
    OrderStatus,
)

_ACTIVE_STATUSES = frozenset({OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.UNKNOWN})


_WORKING_STATUSES = _ACTIVE_STATUSES


_STOP_PURPOSES = frozenset({IntentPurpose.STOP, IntentPurpose.BRACKET})


_ZERO = Decimal(0)


LOCAL_EVIDENCE_MAX_AGE = timedelta(seconds=120)
"""Reporting recency bound: four default worker polls, never a strategy candle clock."""


_ObservationSource = Literal["venue_order_state", "persisted_order", "synthetic_worker", "none"]


_Freshness = Literal["recent_venue", "stale", "unknown"]


_GeometryBasis = Literal["working_target", "stop_limit_trigger", "unknown"]


PROTECTION_REASONS: tuple[str, ...] = (
    "flat",
    "inventory_projection_unresolved",
    "runtime_position_unresolved",
    "fill_economics_unsettled",
    "inventory_evidence_incomplete",
    "synthetic_worker_dependent",
    "venue_stop_resting",
    "duplicate_order_ignored",
    "take_profit_only",
    "closing_side_mismatch",
    "stop_price_mismatch",
    "stale_bracket",
    "stop_geometry_invalid",
    "stop_geometry_unknown",
    "unsupported_stop_kind",
    "venue_identity_missing",
    "local_observation_only",
    "venue_evidence_stale",
    "observation_time_unknown",
    "observation_time_future",
    "stop_quantity_short",
    "partial_stop_quantity",
    "pending_not_confirmed",
    "unknown_not_confirmed",
    "no_resting_stop",
)
"""Stable reason codes for protection evidence, in display order."""


class ProtectionStatus(StrEnum):
    """Whether fresh order-state evidence matches persisted stop cover, not a venue audit."""

    FLAT = "flat"
    COVERED = "covered"
    UNPROTECTED = "unprotected"
    UNKNOWN = "unknown"


class ProtectionMechanism(StrEnum):
    """How cover was established, without treating a synthetic stop as a venue order."""

    VENUE = "venue"
    SYNTHETIC = "synthetic"
    NONE = "none"
    UNVERIFIED = "unverified"


class PositionState(StrEnum):
    """What a book (or a whole deployment) is doing, in operator terms (ADR 0097).

    ``open_protected`` has fresh order-state evidence matching persisted stop geometry or
    the paper synthetic stop. Neither proves a venue geometry audit. ``exiting`` is in flight.
    """

    FLAT = "flat"
    ENTERING = "entering"
    OPEN_PROTECTED = "open_protected"
    OPEN_UNPROTECTED = "open_unprotected"
    OPEN_UNVERIFIED = "open_unverified"
    EXITING = "exiting"


_STATE_PRIORITY: tuple[PositionState, ...] = (
    PositionState.EXITING,
    PositionState.OPEN_UNPROTECTED,
    PositionState.OPEN_UNVERIFIED,
    PositionState.OPEN_PROTECTED,
)


_OPEN_STATE: dict[ProtectionStatus, PositionState] = {
    ProtectionStatus.FLAT: PositionState.FLAT,
    ProtectionStatus.COVERED: PositionState.OPEN_PROTECTED,
    ProtectionStatus.UNPROTECTED: PositionState.OPEN_UNPROTECTED,
    ProtectionStatus.UNKNOWN: PositionState.OPEN_UNVERIFIED,
}


@dataclass(frozen=True, slots=True)
class ProtectionEvidence:
    """Exact persisted stop cover, with venue order-state provenance, not geometry auditing."""

    status: ProtectionStatus
    required_quantity: Decimal | None
    covered_quantity: Decimal | None
    uncovered_quantity: Decimal | None
    stop_side: OrderSide | None
    stop_side_valid: bool
    stop_geometry_valid: bool
    mechanism: ProtectionMechanism
    venue_resting: bool
    worker_dependent: bool
    observed_at: datetime | None
    verified_at: datetime | None
    observation_source: _ObservationSource
    freshness: _Freshness
    evaluated_at: datetime
    freshness_max_age_seconds: int
    geometry_basis: _GeometryBasis
    reasons: tuple[str, ...]


class ProtectionEvidenceResponse(BaseModel):
    """Strict protection evidence; exact decimal strings, or null for unresolved inventory."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    required_quantity: str | None = Field(pattern=r"^\d+(?:\.\d+)?$")
    covered_quantity: str | None = Field(pattern=r"^\d+(?:\.\d+)?$")
    uncovered_quantity: str | None = Field(pattern=r"^\d+(?:\.\d+)?$")
    stop_side: Literal["buy", "sell"] | None = Field(
        description="Closing side the stop must use, or null when flat or inventory is unresolved."
    )
    stop_side_valid: bool
    stop_geometry_valid: bool
    mechanism: Literal["venue", "synthetic", "none", "unverified"]
    venue_resting: bool = Field(
        description="Fresh identified OPEN state reads contributed with matching local geometry."
    )
    worker_dependent: bool = Field(
        description="True for the paper synthetic stop. That cover is not a venue order."
    )
    observed_at: str | None = Field(
        description="Latest relevant venue order-state receipt time; never a local row update."
    )
    verified_at: str | None = Field(
        description="Oldest contributing fresh OPEN receipt; not a venue geometry audit."
    )
    observation_source: _ObservationSource
    freshness: _Freshness = Field(
        description="Conservative order-state recency, not geometry auditing."
    )
    evaluated_at: str = Field(
        description="UTC reporting clock used to assess venue order-state age."
    )
    freshness_max_age_seconds: int = Field(gt=0)
    geometry_basis: _GeometryBasis = Field(
        description="Persisted geometry vs working target or stop-limit trigger, not venue audited."
    )
    reasons: tuple[str, ...]
