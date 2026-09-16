"""HTTP contract for experiential memory journals, monitor, notify, and models."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.memory.evidence import LocalEvidenceResolver
from thytrader.memory.models import (
    ActorOrigin,
    EvidenceKind,
    JournalEntry,
    JournalKind,
    LessonOutcome,
    NotifyProvider,
)
from thytrader.memory.notify import RecordingNotificationSender
from thytrader.memory.store import DisabledExperientialMemoryStore, InMemoryExperientialMemoryStore
from thytrader.memory.trade_reasons import (
    TradeReasonOrigin,
    TradeReasonRecord,
    TradeReasonRisk,
    TradeReasonSignal,
    TradeReasonSignalKind,
    TradeReasonStrategy,
)
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


def test_post_model_trains_from_evidenced_journals() -> None:
    """POST /models trains a fingerprintable advisory from local journal evidence."""
    store = InMemoryExperientialMemoryStore()
    asyncio.run(
        store.append_journal(
            JournalEntry(
                occurred_at=datetime(2026, 9, 16, 12, 0, tzinfo=UTC),
                recorded_at=datetime(2026, 9, 16, 12, 0, tzinfo=UTC),
                origin=ActorOrigin.HUMAN,
                kind=JournalKind.LESSON,
                title="Cut size",
                body="BTC lesson with a local backtest.",
                evidence_kind=EvidenceKind.BACKTEST,
                evidence_id="bt-1",
                product_id="BTC-USD",
                lesson_outcome=LessonOutcome.SUCCESS,
            )
        )
    )

    async def exists(_self: LocalEvidenceResolver, kind: EvidenceKind, evidence_id: str) -> bool:
        """Allow only the fixture backtest pointer."""
        return kind is EvidenceKind.BACKTEST and evidence_id == "bt-1"

    app = create_app(
        Settings(_env_file=None),
        memory_store=store,
        audit_event_store=InMemoryAuditEventStore(),
    )
    with (
        patch.object(LocalEvidenceResolver, "exists", exists),
        TestClient(app) as client,
    ):
        created = client.post("/api/v1/memory/models", json={"origin": "agent", "seed": 1})
        listed = client.get("/api/v1/memory/models")
        shown = client.get(f"/api/v1/memory/models/{created.json()['id']}")
    assert created.status_code == 201
    body = created.json()
    assert body["schema_version"] == "thytrader-experiential-model-v1"
    assert body["engine_id"] == "thytrader-experiential-train-v1"
    assert body["advisory"]["suggested_products"] == ["BTC-USD"]
    assert listed.status_code == 200
    assert listed.json()["models"][0]["id"] == body["id"]
    assert shown.status_code == 200
    assert shown.json()["fingerprint"] == body["fingerprint"]


def test_post_model_fails_closed_without_store() -> None:
    """Training returns 503 when experiential memory storage is disabled."""
    app = create_app(
        Settings(_env_file=None),
        memory_store=DisabledExperientialMemoryStore(),
        audit_event_store=InMemoryAuditEventStore(),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/v1/memory/models", json={"origin": "human"})
    assert response.status_code == 503


def test_get_model_returns_404_when_missing() -> None:
    """Show-model is fail-closed on an unknown id."""
    app = create_app(
        Settings(_env_file=None),
        memory_store=InMemoryExperientialMemoryStore(),
        audit_event_store=InMemoryAuditEventStore(),
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/memory/models/11111111-1111-1111-1111-111111111111")
    assert response.status_code == 404


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


_FP = "sha256:" + ("c" * 64)


def test_trade_reasons_list_show_and_note() -> None:
    """GET list/show and POST notes share the composed why-trade payload."""
    store = InMemoryExperientialMemoryStore()
    intent_id = uuid4()
    now = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
    asyncio.run(
        store.append_trade_reason(
            TradeReasonRecord(
                created_at=now,
                origin=TradeReasonOrigin.RUNTIME,
                intent_id=intent_id,
                deployment_id=uuid4(),
                deployment_kind="strategy",
                mode="paper",
                product_id="BTC-USD",
                purpose="entry",
                side="buy",
                strategy=TradeReasonStrategy(
                    strategy_id=uuid4(),
                    strategy_fingerprint=_FP,
                    name="ref",
                    version=1,
                ),
                signal=TradeReasonSignal(
                    kind=TradeReasonSignalKind.STRATEGY_ENTRY,
                    last_signal="matched",
                    candle_starts_at=now,
                    timeframe="1h",
                ),
                risk=TradeReasonRisk(
                    decision="allow",
                    reason_code="ALLOWED",
                    detail="Admitted.",
                    policy_fingerprint=_FP,
                    policy_source="compiled_default",
                ),
            )
        )
    )
    app = create_app(
        Settings(_env_file=None),
        memory_store=store,
        audit_event_store=InMemoryAuditEventStore(),
    )
    with TestClient(app) as client:
        listed = client.get("/api/v1/memory/trade-reasons")
        shown = client.get(f"/api/v1/memory/trade-reasons/{intent_id}")
        noted = client.post(
            f"/api/v1/memory/trade-reasons/{intent_id}/notes",
            json={"origin": "human", "body": "Reviewed the fade."},
        )
        missing = client.get("/api/v1/memory/trade-reasons/11111111-1111-1111-1111-111111111111")
    assert listed.status_code == 200
    assert listed.json()["schema_version"] == "thytrader-trade-reason-v1"
    assert listed.json()["trade_reasons"][0]["intent_id"] == str(intent_id)
    assert shown.status_code == 200
    assert shown.json()["risk"]["decision"] == "allow"
    assert noted.status_code == 201
    assert noted.json()["notes"][0]["body"] == "Reviewed the fade."
    assert missing.status_code == 404
