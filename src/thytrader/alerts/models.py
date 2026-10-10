"""Typed domain records for durable operator safety alerts (ADR 0115)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from datetime import datetime

_DELIVERY_PENDING = "pending"


class AlertSeverity(StrEnum):
    """Operator-visible urgency of one alert."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertScope(StrEnum):
    """What one alert subject names: a single book, the worker, or a fleet entry scope.

    A ``fleet`` subject is ``fleet:<mode>:<scope>``, for example ``fleet:live:USDC``
    (ADR 0130).
    """

    DEPLOYMENT = "deployment"
    WORKER = "worker"
    FLEET = "fleet"


class AlertCode(StrEnum):
    """Stable reason codes for durable safety alerts.

    ``WORKER_BOOK_FAILURES`` counts observed failed cycles since a verified
    successful decision, not since an ambiguous no-op or lease skip. Counts
    survive restart; observing the persisted entry-pause latch keeps evidence
    open without fabricating another raised error.

    ``FLEET_ENTRIES_BLOCKED`` (ADR 0130) is one mode and quote scope in which the entry
    gate denies every new entry for a reason that does not clear by itself. It resolves on
    the first complete evaluation that finds the scope admissible again.
    """

    BOOK_PAUSED_MISMATCH = "BOOK_PAUSED_MISMATCH"
    BREAKER_LATCHED = "BREAKER_LATCHED"
    STOP_UNCOVERED = "STOP_UNCOVERED"
    STOP_COVERAGE_UNKNOWN = "STOP_COVERAGE_UNKNOWN"
    STOP_TRIGGERED_UNFILLED = "STOP_TRIGGERED_UNFILLED"
    DECISION_DEADLINE_MISSED = "DECISION_DEADLINE_MISSED"
    MAINTENANCE_DEADLINE_MISSED = "MAINTENANCE_DEADLINE_MISSED"
    WORKER_LEASE_STALE = "WORKER_LEASE_STALE"
    WORKER_BOOK_FAILURES = "WORKER_BOOK_FAILURES"
    FLEET_ENTRIES_BLOCKED = "FLEET_ENTRIES_BLOCKED"


#: Delivery outcome values persisted on alert rows. ``skipped`` means the
#: configured provider is ``none`` (local feed only); ``exhausted`` means the
#: retry budget was spent without a delivered result.
DELIVERY_STATUSES: frozenset[str] = frozenset(
    {"pending", "skipped", "logged", "delivered", "failed", "exhausted"}
)


@dataclass(frozen=True, slots=True)
class SupervisionFinding:
    """One supervision observation handed to the alert service in a cycle.

    Findings are recomputed from durable state every cycle; the alert service
    deduplicates them into open alert rows, so repeated findings increment an
    occurrence counter instead of flooding a new row per cycle.
    """

    code: AlertCode
    scope: AlertScope
    subject: str
    severity: AlertSeverity
    detail: str
    deployment_id: UUID | None = None
    product_id: str | None = None
    count_occurrence: bool = True

    @property
    def identity(self) -> tuple[AlertCode, str]:
        """The dedupe identity: one open alert row per code and subject."""
        return (self.code, self.subject)


@dataclass(frozen=True, slots=True)
class OperatorAlert:
    """One durable alert row with dedupe, recovery, and delivery state.

    ``occurrences`` counts accepted newer positive observations while open.
    Unknown passes preserve it, and persisted pause observations do not increment
    the worker error count. ``resolved_at`` is set exactly once
    when the condition clears; a later recurrence opens a new row so recovery
    history stays append-oriented.
    """

    id: UUID
    code: AlertCode
    scope: AlertScope
    subject: str
    severity: AlertSeverity
    detail: str
    first_seen_at: datetime
    last_seen_at: datetime
    occurrences: int
    resolved_at: datetime | None = None
    resolution_detail: str = ""
    deployment_id: UUID | None = None
    product_id: str | None = None
    delivery_provider: str = "none"
    delivery_status: str = _DELIVERY_PENDING
    delivery_attempts: int = 0
    delivery_detail: str = ""
    delivery_token: UUID | None = None
    delivery_expires_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        """True while the condition has not been observed cleared."""
        return self.resolved_at is None


@dataclass(frozen=True, slots=True)
class AlertCheck:
    """An exact condition identity that was re-evaluated with complete evidence."""

    code: AlertCode
    subject: str


@dataclass(frozen=True, slots=True)
class SafetyEvidence:
    """Positive findings and checks whose absence can truthfully prove recovery.

    Missing checks mean unknown, not healthy. Inventory removal is a recovery
    only when the caller explicitly supplies an authoritative complete inventory.
    """

    findings: tuple[SupervisionFinding, ...] = ()
    evaluated: tuple[AlertCheck, ...] = ()


def clip_alert_text(text: str, *, limit: int = 500) -> str:
    """Keep persisted alert text inside the durable column bound."""
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def new_alert(finding: SupervisionFinding, *, now: datetime) -> OperatorAlert:
    """Build the first open row for one finding."""
    return OperatorAlert(
        id=uuid4(),
        code=finding.code,
        scope=finding.scope,
        subject=finding.subject,
        severity=finding.severity,
        detail=clip_alert_text(finding.detail),
        first_seen_at=now,
        last_seen_at=now,
        occurrences=1,
        deployment_id=finding.deployment_id,
        product_id=finding.product_id,
    )
