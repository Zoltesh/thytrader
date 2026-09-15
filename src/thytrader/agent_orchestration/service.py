"""Build orchestration status from settings and record YOLO confirmation skips."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from thytrader.agent_orchestration.models import (
    PLAYBOOK_SEQUENCE,
    AgentOrchestrationStatus,
    ConfirmationMode,
    SkippedConfirmationRequest,
    SkippedConfirmationResponse,
    YoloTier,
)
from thytrader.persistence.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventUnavailableError,
    DisabledAuditEventStore,
)

if TYPE_CHECKING:
    from thytrader.config import Settings
    from thytrader.persistence.audit_events import AuditEventStore

_TIER_CATEGORY: dict[YoloTier, AuditEventCategory] = {
    YoloTier.DATA: AuditEventCategory.MARKET_DATA,
    YoloTier.RESEARCH: AuditEventCategory.RESEARCH,
    YoloTier.PAPER: AuditEventCategory.RUNTIME,
}


class YoloSkipRejectedError(RuntimeError):
    """Refuse a skipped confirmation when YOLO does not cover the requested tier."""


def orchestration_status(settings: Settings) -> AgentOrchestrationStatus:
    """Advertise Safe vs YOLO without granting live authority."""
    enabled = settings.yolo_enabled
    return AgentOrchestrationStatus(
        confirmation_mode=ConfirmationMode.YOLO if enabled else ConfirmationMode.SAFE,
        yolo_enabled=enabled,
        yolo_tiers=settings.yolo_tiers,
        playbook_sequence=PLAYBOOK_SEQUENCE,
    )


async def record_skipped_confirmation(
    *,
    settings: Settings,
    store: AuditEventStore,
    request: SkippedConfirmationRequest,
) -> SkippedConfirmationResponse:
    """Append one skipped-confirmation audit event, or fail closed.

    Disabled audit storage is treated as unavailable. YOLO that cannot be
    audited must not skip `--confirm`.
    """
    status = orchestration_status(settings)
    if not status.allows(request.tier):
        raise YoloSkipRejectedError(
            "YOLO is not enabled for this tier. Pass --confirm. Live keeps a hard gate."
        )
    if isinstance(store, DisabledAuditEventStore):
        raise AuditEventUnavailableError(
            "Audit event storage is unavailable; YOLO cannot skip --confirm."
        )
    category = _TIER_CATEGORY[request.tier]
    event = AuditEvent(
        occurred_at=datetime.now(UTC),
        category=category,
        action="confirm_skipped",
        outcome=AuditEventOutcome.INFO,
        detail=(
            f"tier={request.tier.value} command={request.command} "
            f"yolo_tiers={','.join(tier.value for tier in status.yolo_tiers)}"
        )[:2048],
    )
    await store.append(event)
    return SkippedConfirmationResponse(
        id=event.id,
        category=category.value,
        tier=request.tier,
        command=request.command,
    )
