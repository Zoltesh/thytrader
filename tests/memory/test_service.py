"""Service tests for journals, notify skip, and monitor findings."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from thytrader.config import Settings
from thytrader.execution.store import DisabledExecutionStore
from thytrader.memory.models import (
    ActorOrigin,
    DeliveryStatus,
    JournalKind,
    JournalWrite,
    NotificationWrite,
    NotifyProvider,
    NotifySeverity,
)
from thytrader.memory.notify import DisabledNotificationSender, RecordingNotificationSender
from thytrader.memory.service import build_monitor, record_journal, submit_notification
from thytrader.memory.store import InMemoryExperientialMemoryStore
from thytrader.persistence.audit_events import AuditEventCategory, InMemoryAuditEventStore


def test_record_journal_persists_origin_and_audits() -> None:
    """Journal writes store origin and append a memory audit event."""
    store = InMemoryExperientialMemoryStore()
    audit = InMemoryAuditEventStore()
    entry = asyncio.run(
        record_journal(
            store,
            audit,
            JournalWrite(
                origin=ActorOrigin.AGENT,
                kind=JournalKind.NOTE,
                title="Paused",
                body="Stale feed.",
            ),
            now=datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
        )
    )
    assert entry.origin is ActorOrigin.AGENT
    events = asyncio.run(audit.list_recent())
    assert events[0].category is AuditEventCategory.MEMORY
    assert events[0].action == "journal_appended"


def test_notify_default_skips_without_sending() -> None:
    """Provider none persists skipped delivery and does not call a webhook."""
    store = InMemoryExperientialMemoryStore()
    audit = InMemoryAuditEventStore()
    sender = DisabledNotificationSender()
    record = asyncio.run(
        submit_notification(
            store,
            audit,
            sender,
            NotificationWrite(
                origin=ActorOrigin.HUMAN,
                title="Paper paused",
                body="Check candles.",
                severity=NotifySeverity.WARNING,
            ),
        )
    )
    assert record.provider is NotifyProvider.NONE
    assert record.delivery_status is DeliveryStatus.SKIPPED
    recording = RecordingNotificationSender()
    assert recording.delivered == []


def test_monitor_does_not_treat_skipped_notify_as_failed() -> None:
    """Default-off notify is not a monitor failure."""
    store = InMemoryExperientialMemoryStore()
    settings = Settings(_env_file=None)
    snapshot = asyncio.run(
        build_monitor(store, DisabledExecutionStore(), settings, storage="available")
    )
    assert snapshot.memory.notify_provider is NotifyProvider.NONE
    assert snapshot.memory.notify_enabled is False
    assert all(item.reason_code != "NOTIFICATION_FAILED" for item in snapshot.findings)
