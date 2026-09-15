"""HTTP contract for experiential memory journals, monitor, and notify."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.memory.models import NotifyProvider
from thytrader.memory.notify import RecordingNotificationSender
from thytrader.memory.store import DisabledExperientialMemoryStore, InMemoryExperientialMemoryStore
from thytrader.persistence.audit_events import InMemoryAuditEventStore


def test_memory_status_reports_unavailable_storage_without_database() -> None:
    """GET /api/v1/memory works when PostgreSQL is unconfigured."""
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        response = client.get("/api/v1/memory")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "thytrader-experiential-memory-v1"
    assert payload["storage"] == "unavailable"
    assert payload["notify_provider"] == "none"
    assert payload["notify_webhook_configured"] is False


def test_post_journal_persists_origin_and_lists() -> None:
    """POST /journals requires origin and returns the stored row."""
    store = InMemoryExperientialMemoryStore()
    audit = InMemoryAuditEventStore()
    app = create_app(
        Settings(_env_file=None),
        memory_store=store,
        audit_event_store=audit,
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/memory/journals",
            json={
                "origin": "agent",
                "kind": "note",
                "title": "Paused paper",
                "body": "Stale candles.",
            },
        )
        listed = client.get("/api/v1/memory/journals")
    assert created.status_code == 201
    body = created.json()
    assert body["origin"] == "agent"
    assert body["kind"] == "note"
    assert listed.status_code == 200
    assert listed.json()["journals"][0]["id"] == body["id"]
    events = asyncio.run(audit.list_recent())
    assert events[0].category.value == "memory"


def test_post_journal_fails_closed_without_store() -> None:
    """Writes return 503 when experiential memory storage is disabled."""
    app = create_app(
        Settings(_env_file=None),
        memory_store=DisabledExperientialMemoryStore(),
        audit_event_store=InMemoryAuditEventStore(),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/memory/journals",
            json={
                "origin": "human",
                "kind": "note",
                "title": "Paused",
                "body": "Stale.",
            },
        )
    assert response.status_code == 503


def test_notify_skipped_with_recording_sender() -> None:
    """Notify persists through a fake sender and never requires a webhook URL."""
    store = InMemoryExperientialMemoryStore()
    sender = RecordingNotificationSender(provider=NotifyProvider.LOG)
    app = create_app(
        Settings(_env_file=None),
        memory_store=store,
        audit_event_store=InMemoryAuditEventStore(),
        notification_sender=sender,
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/memory/notifications",
            json={"origin": "human", "title": "Hello", "body": "Check paper."},
        )
        monitor = client.get("/api/v1/memory/monitor")
    assert response.status_code == 201
    assert response.json()["delivery_status"] == "logged"
    assert len(sender.delivered) == 1
    assert monitor.status_code == 200
    assert monitor.json()["schema_version"] == "thytrader-monitor-v1"
    assert all(
        finding["reason_code"] != "NOTIFICATION_FAILED" for finding in monitor.json()["findings"]
    )
