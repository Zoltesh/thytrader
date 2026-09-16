"""Record journals, sentiment, pattern hooks, and confirmation-gated notifications."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from thytrader.execution.ids import utc_now
from thytrader.execution.models import DeploymentStatus, ExecutionStoreError
from thytrader.memory.models import (
    ActorOrigin,
    DeliveryStatus,
    JournalEntry,
    JournalKind,
    JournalWrite,
    MemoryCounts,
    MemoryStatus,
    MonitorDeployment,
    MonitorFinding,
    MonitorSnapshot,
    NotificationRecord,
    NotificationWrite,
    NotifyProvider,
    PatternObservation,
    PatternWrite,
    SentimentSnapshot,
    SentimentWrite,
)
from thytrader.memory.recording import (
    append_attributed_note,
    compose_trade_reasons,
)
from thytrader.memory.store import (
    DisabledExperientialMemoryStore,
    ExperientialMemoryStore,
)
from thytrader.memory.trade_reasons import (
    TradeReasonNoteOrigin,
    TradeReasonOrigin,
    TradeReasonRecord,
)
from thytrader.persistence.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    AuditEventStore,
)

if TYPE_CHECKING:
    from uuid import UUID

    from thytrader.config import Settings
    from thytrader.execution.store import ExecutionStore
    from thytrader.memory.notify import NotificationSender


def memory_status(
    *,
    counts_journals: int,
    counts_sentiment: int,
    counts_patterns: int,
    counts_notifications: int,
    counts_trade_reasons: int,
    settings: Settings,
    storage: str,
) -> MemoryStatus:
    """Build redacted memory status from counts and Settings."""
    provider = settings.notify_provider
    return MemoryStatus(
        counts=MemoryCounts(
            journals=counts_journals,
            sentiment=counts_sentiment,
            patterns=counts_patterns,
            notifications=counts_notifications,
            trade_reasons=counts_trade_reasons,
        ),
        notify_provider=provider,
        notify_webhook_configured=settings.notify_webhook_url is not None,
        notify_enabled=provider is not NotifyProvider.NONE,
        storage="available" if storage == "available" else "unavailable",
    )


async def load_memory_status(
    store: ExperientialMemoryStore,
    settings: Settings,
    *,
    storage: str,
) -> MemoryStatus:
    """Load counts and redacted notifier flags."""
    counts = await store.counts()
    return memory_status(
        counts_journals=counts.journals,
        counts_sentiment=counts.sentiment,
        counts_patterns=counts.patterns,
        counts_notifications=counts.notifications,
        counts_trade_reasons=counts.trade_reasons,
        settings=settings,
        storage=storage,
    )


async def record_journal(
    store: ExperientialMemoryStore,
    audit: AuditEventStore,
    write: JournalWrite,
    *,
    now: datetime | None = None,
) -> JournalEntry:
    """Validate and append one origin-attributed journal row."""
    instant = now or utc_now()
    entry = JournalEntry(
        occurred_at=instant,
        recorded_at=instant,
        origin=write.origin,
        kind=write.kind,
        title=write.title,
        body=write.body,
        evidence_kind=write.evidence_kind,
        evidence_id=write.evidence_id,
        product_id=write.product_id,
        runtime_mode=write.runtime_mode,
        lesson_outcome=write.lesson_outcome,
    )
    stored = await store.append_journal(entry)
    await _audit(
        audit,
        action="journal_appended",
        detail=f"origin={stored.origin.value} kind={stored.kind.value} id={stored.id}",
        product_id=stored.product_id,
        occurred_at=instant,
    )
    return stored


async def record_sentiment(
    store: ExperientialMemoryStore,
    audit: AuditEventStore,
    write: SentimentWrite,
    *,
    now: datetime | None = None,
) -> SentimentSnapshot:
    """Validate and append one sentiment hook."""
    instant = now or utc_now()
    snapshot = SentimentSnapshot(
        occurred_at=instant,
        recorded_at=instant,
        origin=write.origin,
        label=write.label,
        product_id=write.product_id,
        note=write.note,
        journal_id=write.journal_id,
    )
    stored = await store.append_sentiment(snapshot)
    await _audit(
        audit,
        action="sentiment_recorded",
        detail=f"origin={stored.origin.value} label={stored.label.value} id={stored.id}",
        product_id=stored.product_id,
        occurred_at=instant,
    )
    return stored


async def record_pattern(
    store: ExperientialMemoryStore,
    audit: AuditEventStore,
    write: PatternWrite,
    *,
    now: datetime | None = None,
) -> PatternObservation:
    """Validate and append one pattern-learning hook."""
    instant = now or utc_now()
    observation = PatternObservation(
        occurred_at=instant,
        recorded_at=instant,
        origin=write.origin,
        pattern_key=write.pattern_key,
        name=write.name,
        hypothesis=write.hypothesis,
        status=write.status,
        evidence_kind=write.evidence_kind,
        evidence_id=write.evidence_id,
        note=write.note,
    )
    stored = await store.append_pattern(observation)
    await _audit(
        audit,
        action="pattern_recorded",
        detail=(
            f"origin={stored.origin.value} pattern_key={stored.pattern_key} "
            f"status={stored.status.value} id={stored.id}"
        ),
        occurred_at=instant,
    )
    return stored


async def submit_notification(
    store: ExperientialMemoryStore,
    audit: AuditEventStore,
    sender: NotificationSender,
    write: NotificationWrite,
    *,
    now: datetime | None = None,
) -> NotificationRecord:
    """Persist one notify request after attempting the configured provider."""
    instant = now or utc_now()
    pending = NotificationRecord(
        occurred_at=instant,
        recorded_at=instant,
        origin=write.origin,
        title=write.title,
        body=write.body,
        severity=write.severity,
        provider=sender.provider(),
        delivery_status=DeliveryStatus.SKIPPED,
        journal_id=write.journal_id,
    )
    result = await sender.deliver(pending)
    recorded = pending.model_copy(
        update={"delivery_status": result.status, "detail": result.detail}
    )
    stored = await store.append_notification(recorded)
    outcome = (
        AuditEventOutcome.FAILURE
        if stored.delivery_status is DeliveryStatus.FAILED
        else AuditEventOutcome.SUCCESS
    )
    await _audit(
        audit,
        action="notification_requested",
        detail=(
            f"origin={stored.origin.value} provider={stored.provider.value} "
            f"status={stored.delivery_status.value} id={stored.id}"
        ),
        occurred_at=instant,
        outcome=outcome,
    )
    return stored


async def build_monitor(
    store: ExperientialMemoryStore,
    execution: ExecutionStore,
    settings: Settings,
    *,
    storage: str,
) -> MonitorSnapshot:
    """Compose deployments, recent memory, and notify findings without sending mail."""
    status = await load_memory_status(store, settings, storage=storage)
    journals = await store.list_journals(limit=10)
    notifications = await store.list_notifications(limit=10)
    raw_reasons = await store.list_trade_reasons(limit=10)
    reasons = await compose_trade_reasons(raw_reasons, execution)
    deployments, deployment_error = await _load_deployments(execution)
    findings = _monitor_findings(
        status=status,
        deployments=deployments,
        notifications=notifications,
        deployment_error=deployment_error,
    )
    return MonitorSnapshot(
        memory=status,
        deployments=deployments,
        recent_journals=journals,
        recent_notifications=notifications,
        recent_trade_reasons=reasons,
        findings=findings,
    )


async def _load_deployments(
    execution: ExecutionStore,
) -> tuple[tuple[MonitorDeployment, ...], str | None]:
    """List deployments or record that execution storage failed."""
    try:
        rows = await execution.list_deployments()
    except ExecutionStoreError:
        return (), "execution_unavailable"
    mapped = tuple(
        MonitorDeployment(
            deployment_id=item.id,
            mode=item.mode.value,
            status=item.status.value,
            product_id=item.product_id,
            mismatch_present=item.mismatch_detail is not None,
        )
        for item in rows
    )
    return mapped, None


def _monitor_findings(
    *,
    status: MemoryStatus,
    deployments: tuple[MonitorDeployment, ...],
    notifications: tuple[NotificationRecord, ...],
    deployment_error: str | None,
) -> tuple[MonitorFinding, ...]:
    """Build stable monitor reason codes without treating default-off notify as failed."""
    findings: list[MonitorFinding] = []
    if status.storage == "unavailable":
        findings.append(
            MonitorFinding(
                reason_code="MEMORY_STORAGE_UNAVAILABLE",
                detail="Experiential memory has no durable store; journal writes fail closed.",
            )
        )
    if deployment_error is not None:
        findings.append(
            MonitorFinding(
                reason_code="EXECUTION_UNAVAILABLE",
                detail="Execution storage is unavailable; monitor omitted deployments.",
            )
        )
    for item in deployments:
        if item.status == DeploymentStatus.PAUSED.value:
            findings.append(
                MonitorFinding(
                    reason_code="DEPLOYMENT_PAUSED",
                    detail=f"{item.mode} deployment {item.deployment_id} is paused.",
                    deployment_id=item.deployment_id,
                )
            )
        if item.mismatch_present:
            findings.append(
                MonitorFinding(
                    reason_code="DEPLOYMENT_MISMATCH",
                    detail=f"{item.mode} deployment {item.deployment_id} has a mismatch.",
                    deployment_id=item.deployment_id,
                )
            )
    failed = [row for row in notifications if row.delivery_status is DeliveryStatus.FAILED]
    if failed:
        findings.append(
            MonitorFinding(
                reason_code="NOTIFICATION_FAILED",
                detail=f"{len(failed)} recent notification attempt(s) failed.",
            )
        )
    return tuple(findings)


async def _audit(
    audit: AuditEventStore,
    *,
    action: str,
    detail: str,
    occurred_at: datetime,
    product_id: str | None = None,
    outcome: AuditEventOutcome = AuditEventOutcome.SUCCESS,
) -> None:
    """Append a memory-category audit event. Disabled stores skip silently."""
    event = AuditEvent(
        occurred_at=occurred_at if occurred_at.tzinfo is UTC else datetime.now(UTC),
        category=AuditEventCategory.MEMORY,
        action=action,
        outcome=outcome,
        detail=detail,
        product_id=product_id,
    )
    await audit.append(event)


def storage_label(store: ExperientialMemoryStore) -> Literal["available", "unavailable"]:
    """Describe whether writes can persist."""
    if isinstance(store, DisabledExperientialMemoryStore):
        return "unavailable"
    return "available"


def origin_or_none(value: str | None) -> ActorOrigin | None:
    """Parse an optional origin filter."""
    if value is None:
        return None
    return ActorOrigin(value)


def kind_or_none(value: str | None) -> JournalKind | None:
    """Parse an optional journal kind filter."""
    if value is None:
        return None
    return JournalKind(value)


def trade_reason_origin_or_none(value: str | None) -> TradeReasonOrigin | None:
    """Parse an optional why-trade origin filter."""
    if value is None:
        return None
    return TradeReasonOrigin(value)


async def load_trade_reasons(
    store: ExperientialMemoryStore,
    execution: ExecutionStore,
    *,
    origin: TradeReasonOrigin | None = None,
    deployment_id: UUID | None = None,
    intent_id: UUID | None = None,
    limit: int = 50,
) -> tuple[TradeReasonRecord, ...]:
    """List composed why-trade records for UI and operator reports."""
    rows = await store.list_trade_reasons(
        origin=origin,
        deployment_id=deployment_id,
        intent_id=intent_id,
        limit=limit,
    )
    return await compose_trade_reasons(rows, execution)


async def load_trade_reason(
    store: ExperientialMemoryStore,
    execution: ExecutionStore,
    intent_id: UUID,
) -> TradeReasonRecord | None:
    """Return one composed why-trade record, if present."""
    row = await store.get_trade_reason_by_intent(intent_id)
    if row is None:
        return None
    composed = await compose_trade_reasons((row,), execution)
    return composed[0]


async def record_trade_reason_note(
    store: ExperientialMemoryStore,
    execution: ExecutionStore,
    *,
    intent_id: UUID,
    origin: TradeReasonNoteOrigin,
    body: str,
    now: datetime | None = None,
) -> TradeReasonRecord:
    """Append a confirmation-gated note and return the composed review payload."""
    stored = await append_attributed_note(
        store, intent_id=intent_id, origin=origin, body=body, now=now
    )
    composed = await compose_trade_reasons((stored,), execution)
    return composed[0]
