"""HTTP contract for the read-only safety-alert feed."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from thytrader.alerts.models import (
    AlertCode,
    AlertScope,
    AlertSeverity,
    SupervisionFinding,
)
from thytrader.alerts.store import InMemoryAlertStore
from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.memory.models import NotifyProvider


def test_alerts_route_is_read_only_and_warns_when_delivery_is_disabled() -> None:
    """GET /api/v1/operator/alerts is read-only and surfaces the delivery warning."""
    store = InMemoryAlertStore()

    async def seed() -> None:
        await store.record(
            SupervisionFinding(
                code=AlertCode.STOP_UNCOVERED,
                scope=AlertScope.DEPLOYMENT,
                subject=str(uuid4()),
                severity=AlertSeverity.CRITICAL,
                detail="open live book has no verified resting exit",
                product_id="BTC-USD",
            ),
            now=datetime(2026, 3, 2, tzinfo=UTC),
        )

    asyncio.run(seed())
    app = create_app(
        settings=Settings(notify_provider=NotifyProvider.NONE, _env_file=None),
        alert_store=store,
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/operator/alerts")
    assert response.status_code == 200
    body = response.json()
    assert body["report_kind"] == "alerts"
    assert body["overall_status"] == "failed"
    assert body["payload"]["storage"] == "available"
    assert body["payload"]["delivery_enabled"] is False
    assert "notify_provider=none" in body["payload"]["delivery_warning"]
    assert body["payload"]["open_critical"] == 1
    assert "webhook" not in response.text.lower() or "not sent to a webhook" in response.text


def test_alerts_route_reports_unavailable_storage_without_inventing_rows() -> None:
    """Without an alert store the report says storage unavailable and lists no rows."""
    app = create_app(settings=Settings(_env_file=None))
    with TestClient(app) as client:
        response = client.get("/api/v1/operator/alerts")
    assert response.status_code == 200
    payload = response.json()["payload"]
    assert payload["storage"] == "unavailable"
    assert payload["open_alerts"] == []


def test_bounded_alert_display_cannot_hide_a_critical_health_result() -> None:
    """Counts/status use full inventory even when the UI feed page is bounded."""
    store = InMemoryAlertStore()

    async def seed() -> None:
        """Place the oldest critical row behind more than one report page of warnings."""
        for index in range(201):
            await store.record(
                SupervisionFinding(
                    code=AlertCode.STOP_UNCOVERED,
                    scope=AlertScope.DEPLOYMENT,
                    subject=str(uuid4()),
                    severity=AlertSeverity.CRITICAL if index == 0 else AlertSeverity.WARNING,
                    detail="bounded feed test",
                ),
                now=datetime(2026, 3, 2, tzinfo=UTC) + timedelta(seconds=index),
            )

    asyncio.run(seed())
    app = create_app(settings=Settings(_env_file=None), alert_store=store)
    with TestClient(app) as client:
        report = client.get("/api/v1/operator/alerts").json()
    assert report["payload"]["open_total"] == 201
    assert report["payload"]["open_critical"] == 1
    assert len(report["payload"]["open_alerts"]) == 200
    assert report["payload"]["open_alerts"][0]["severity"] == "critical"
    assert report["overall_status"] == "failed"
    assert any("bounded" in warning for warning in report["partial_result_warnings"])
