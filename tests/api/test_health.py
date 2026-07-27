"""Behavioral tests for ThyTrader health endpoints."""

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.config import Settings


def test_liveness_reports_the_running_api() -> None:
    """The liveness endpoint should identify a running API process."""
    app = create_app(Settings(_env_file=None))

    with TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {
        "service": "api",
        "status": "ok",
        "version": "0.1.0",
    }


def test_readiness_reports_ready_after_startup() -> None:
    """The readiness endpoint should succeed after application startup."""
    app = create_app(Settings(_env_file=None))

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "service": "api",
        "status": "ready",
        "version": "0.1.0",
    }


def test_readiness_rejects_traffic_before_startup() -> None:
    """The readiness endpoint should fail until application startup completes."""
    client = TestClient(create_app(Settings(_env_file=None)))

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "service": "api",
        "status": "not_ready",
        "version": "0.1.0",
    }
