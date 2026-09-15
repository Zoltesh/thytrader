"""HTTP contract for agent orchestration status and YOLO skip audits."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from thytrader.agent_orchestration.models import YoloTier
from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.persistence.audit_events import DisabledAuditEventStore, InMemoryAuditEventStore


def test_get_orchestration_defaults_to_safe() -> None:
    """GET /api/v1/agent-orchestration advertises Safe mode with a live hard gate."""
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        response = client.get("/api/v1/agent-orchestration")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "thytrader-agent-orchestration-v1"
    assert payload["confirmation_mode"] == "safe"
    assert payload["yolo_enabled"] is False
    assert payload["yolo_tiers"] == []
    assert payload["live_hard_gate"] is True
    assert payload["live_authority"] is False
    assert payload["playbook_sequence"] == [
        "data_healthy",
        "draft_publish",
        "backtest",
        "optional_paper",
    ]


def test_post_skipped_confirmation_forbidden_when_yolo_off() -> None:
    """Safe mode rejects skip audits."""
    store = InMemoryAuditEventStore()
    app = create_app(Settings(_env_file=None), audit_event_store=store)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent-orchestration/skipped-confirmations",
            json={"tier": "data", "command": "watch-add"},
        )
    assert response.status_code == 403
    assert asyncio.run(store.list_recent()) == ()


def test_post_skipped_confirmation_records_when_yolo_on() -> None:
    """Enabled YOLO for data writes a skipped-confirmation audit event."""
    store = InMemoryAuditEventStore()
    settings = Settings(yolo_enabled=True, yolo_tiers=(YoloTier.DATA,), _env_file=None)
    app = create_app(settings, audit_event_store=store)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent-orchestration/skipped-confirmations",
            json={"tier": "data", "command": "ingest"},
        )
    assert response.status_code == 201
    body = response.json()
    assert body["action"] == "confirm_skipped"
    assert body["category"] == "market_data"
    assert body["tier"] == "data"
    events = asyncio.run(store.list_recent())
    assert len(events) == 1
    assert events[0].action == "confirm_skipped"


def test_post_skipped_confirmation_unavailable_without_store() -> None:
    """YOLO skip fails closed when audit storage is disabled."""
    settings = Settings(yolo_enabled=True, yolo_tiers=(YoloTier.PAPER,), _env_file=None)
    app = create_app(settings, audit_event_store=DisabledAuditEventStore())
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/agent-orchestration/skipped-confirmations",
            json={"tier": "paper", "command": "start"},
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "persistence_unavailable"


def test_post_skipped_confirmation_rejects_live_tier() -> None:
    """Live is not a YOLO tier on the wire."""
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/agent-orchestration/skipped-confirmations",
            json={"tier": "live", "command": "start"},
        )
    assert response.status_code == 422
