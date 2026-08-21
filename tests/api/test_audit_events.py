"""Unit tests for the audit events API endpoint."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.persistence.audit_events import (
    AuditEvent,
    AuditEventCategory,
    AuditEventOutcome,
    DisabledAuditEventStore,
    InMemoryAuditEventStore,
)

if TYPE_CHECKING:
    from typing import Any


def _sample_event(action: str = "snapshot_recorded") -> AuditEvent:
    return AuditEvent(
        id=uuid4(),
        occurred_at=datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC),
        category=AuditEventCategory.SNAPSHOT,
        action=action,
        outcome=AuditEventOutcome.SUCCESS,
        detail="Snapshot recorded",
        provider="coinbase",
        product_id=None,
        recorded_at=datetime(2026, 8, 17, 12, 0, 1, tzinfo=UTC),
    )


def test_audit_events_endpoint_with_in_memory_store() -> None:
    """GET /api/v1/audit-events returns the bounded newest-first event list."""
    store = InMemoryAuditEventStore()
    e1 = _sample_event(action="action_1")
    e2 = _sample_event(action="action_2")

    asyncio.run(store.append(e1))
    asyncio.run(store.append(e2))

    app = create_app(Settings(_env_file=None), audit_event_store=store)
    with TestClient(app) as client:
        response = client.get("/api/v1/audit-events?limit=10")

    assert response.status_code == 200
    data = response.json()
    assert "events" in data
    assert len(data["events"]) == 2
    assert data["events"][0]["action"] == "action_2"
    assert data["events"][1]["action"] == "action_1"
    assert data["events"][0]["category"] == "snapshot"
    assert data["events"][0]["outcome"] == "success"


def test_audit_events_endpoint_disabled_store_fails_closed() -> None:
    """GET /api/v1/audit-events with disabled store returns 503 error envelope."""
    app = create_app(Settings(_env_file=None), audit_event_store=DisabledAuditEventStore())
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/audit-events")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "persistence_unavailable",
            "message": "Audit event storage is unavailable.",
        }
    }


def test_audit_events_endpoint_store_boundary_exceptions_redacted() -> None:
    """Store raising RuntimeError/TypeError/ValueError is caught and returns static 503."""

    class HostileStore:
        async def append(self, event: AuditEvent) -> None:
            pass

        async def list_recent(self, *, limit: int = 50) -> Any:
            del limit
            raise TypeError("Sensitive internal database detail")

    app = create_app(Settings(_env_file=None), audit_event_store=HostileStore())  # type: ignore[arg-type]
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/audit-events")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "persistence_unavailable",
            "message": "Audit event storage is unavailable.",
        }
    }


def test_audit_events_endpoint_rejects_forged_store_output() -> None:
    """If the store returns an object that violates the schema, endpoint fails closed 503."""

    class ForgedStore:
        async def append(self, event: AuditEvent) -> None:
            pass

        async def list_recent(self, *, limit: int = 50) -> Any:
            del limit
            return ({"invalid": "data"},)

    app = create_app(Settings(_env_file=None), audit_event_store=ForgedStore())  # type: ignore[arg-type]
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/audit-events")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "persistence_unavailable",
            "message": "Audit event storage is unavailable.",
        }
    }


def test_audit_events_endpoint_rejects_forged_naive_datetimes() -> None:
    """Hostile store events with naive timestamps must fail closed 503."""

    class NaiveDateTimeEvent:
        def __init__(self) -> None:
            self.id = uuid4()
            # Intentionally naive: hostile store payload under test.
            self.occurred_at = datetime(2026, 8, 19, 12)  # noqa: DTZ001
            self.category = "snapshot"
            self.action = "portfolio_snapshot_recorded"
            self.outcome = "success"
            self.detail = ""
            self.provider = "coinbase"
            self.product_id = None
            self.recorded_at = datetime(2026, 8, 19, 12)  # noqa: DTZ001

    class ForgedStore:
        async def append(self, event: AuditEvent) -> None:
            pass

        async def list_recent(self, *, limit: int = 50) -> Any:
            del limit
            return (NaiveDateTimeEvent(),)

    app = create_app(Settings(_env_file=None), audit_event_store=ForgedStore())  # type: ignore[arg-type]
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/audit-events")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "persistence_unavailable"


def test_audit_events_endpoint_rejects_forged_zero_offset_non_utc_datetime() -> None:
    """A named zero-offset timezone cannot be serialized as UTC audit evidence."""

    class ForgedZoneEvent:
        def __init__(self) -> None:
            forged_zone = timezone(timedelta(0), "forged-zero-offset-zone")
            self.id = uuid4()
            self.occurred_at = datetime(2026, 8, 19, 12, tzinfo=forged_zone)
            self.category = "snapshot"
            self.action = "portfolio_snapshot_recorded"
            self.outcome = "success"
            self.detail = ""
            self.provider = "coinbase"
            self.product_id = None
            self.recorded_at = datetime(2026, 8, 19, 12, tzinfo=forged_zone)

    class ForgedStore:
        async def append(self, event: AuditEvent) -> None:
            del event

        async def list_recent(self, *, limit: int = 50) -> Any:
            del limit
            return (ForgedZoneEvent(),)

    app = create_app(Settings(_env_file=None), audit_event_store=ForgedStore())  # type: ignore[arg-type]
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/audit-events")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "persistence_unavailable"
