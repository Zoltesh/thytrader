"""HTTP contract for YAML settings and YOLO hot-reload after start."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.persistence.audit_events import InMemoryAuditEventStore
from thytrader.settings_yaml import SettingsStore

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI
    import pytest

_WRITE = {
    "yolo_enabled": False,
    "yolo_tiers": [],
    "log_level": "INFO",
    "snapshot_interval_seconds": 300,
    "market_data_worker_interval_seconds": 300,
    "market_data_worker_lookback_hours": 168,
    "market_data_worker_product_id": "BTC-USD",
    "execution_worker_interval_seconds": 30,
    "notify_provider": "none",
}


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[SettingsStore, FastAPI]:
    """Build an API with a YAML store and no leftover YOLO env."""
    monkeypatch.delenv("THYTRADER_YOLO_ENABLED", raising=False)
    monkeypatch.delenv("THYTRADER_YOLO_TIERS", raising=False)
    store = SettingsStore(tmp_path / "thytrader.yaml", env_file=None)
    return store, create_app(settings_store=store, audit_event_store=InMemoryAuditEventStore())


def test_get_settings_defaults_yolo_off(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GET /api/v1/settings advertises Safe mode and never echoes secrets."""
    _store, app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        response = client.get("/api/v1/settings")
    assert response.status_code == 200
    payload = response.json()
    assert payload["yolo_enabled"] is False
    assert payload["yolo_tiers"] == []
    assert payload["live_hard_gate"] is True
    assert payload["playbook_live_authority"] is False
    assert "coinbase_api_key_name" not in payload
    assert "coinbase_api_private_key" not in payload
    assert "notify_webhook_url" not in payload
    assert "database_url" not in payload
    dumped = response.text.lower()
    assert "postgresql://" not in dumped
    assert "begin private" not in dumped


def test_put_yaml_paper_after_start_allows_paper_skip_not_live(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """YAML ``paper`` after start is enough for paper skip-confirm; live stays gated."""
    _store, app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        before = client.get("/api/v1/agent-orchestration").json()
        assert before["yolo_enabled"] is False
        put = client.put(
            "/api/v1/settings",
            json={**_WRITE, "yolo_enabled": True, "yolo_tiers": ["paper"]},
        )
        assert put.status_code == 200
        assert put.json()["yolo_tiers"] == ["paper"]
        orch = client.get("/api/v1/agent-orchestration").json()
        assert orch["confirmation_mode"] == "yolo"
        assert orch["yolo_tiers"] == ["paper"]
        assert orch["live_hard_gate"] is True
        paper = client.post(
            "/api/v1/agent-orchestration/skipped-confirmations",
            json={"tier": "paper", "command": "start"},
        )
        assert paper.status_code == 201
        live = client.post(
            "/api/v1/agent-orchestration/skipped-confirmations",
            json={"tier": "live", "command": "start"},
        )
        assert live.status_code == 403


def test_put_rejects_secret_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown secret-shaped fields are forbidden on the write contract."""
    _store, app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        response = client.put(
            "/api/v1/settings",
            json={**_WRITE, "notify_webhook_url": "https://example.test"},
        )
    assert response.status_code == 422


def test_leftover_env_scalar_paper_does_not_crash_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``THYTRADER_YOLO_TIERS=paper`` leftover must not fail SettingsStore on boot."""
    monkeypatch.setenv("THYTRADER_YOLO_ENABLED", "true")
    monkeypatch.setenv("THYTRADER_YOLO_TIERS", "paper")
    store = SettingsStore(tmp_path / "thytrader.yaml", env_file=None)
    app = create_app(settings_store=store, audit_event_store=InMemoryAuditEventStore())
    with TestClient(app) as client:
        payload = client.get("/api/v1/settings").json()
    assert payload["yolo_enabled"] is True
    assert payload["yolo_tiers"] == ["paper"]


def test_put_unavailable_without_store() -> None:
    """Test apps that inject Settings without a YAML store cannot persist YAML."""
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        response = client.put("/api/v1/settings", json=_WRITE)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "settings_store_unavailable"
