"""HTTP contract for the read-only futures funding report (ADR 0126)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.config import Settings


def test_funding_route_without_storage_is_degraded_and_empty() -> None:
    """Without a database the report says storage is disabled and lists no history."""
    app = create_app(settings=Settings(_env_file=None))
    with TestClient(app) as client:
        response = client.get("/api/v1/operator/funding", params={"hours": 48})
    assert response.status_code == 200
    body = response.json()
    assert body["report_kind"] == "funding"
    assert body["overall_status"] == "degraded"
    assert body["components"][0]["reason_code"] == "STORE_DISABLED"
    assert body["payload"]["hours"] == 48
    assert body["payload"]["contracts"] == []


def test_funding_route_validates_the_futures_id_and_window() -> None:
    """Spot ids and out-of-range windows are refused before any read."""
    app = create_app(settings=Settings(_env_file=None))
    with TestClient(app) as client:
        assert client.get("/api/v1/operator/funding?product_id=BTC-USD").status_code == 422
        assert client.get("/api/v1/operator/funding?hours=0").status_code == 422
        assert client.get("/api/v1/operator/funding?hours=721").status_code == 422
        scoped = client.get("/api/v1/operator/funding?product_id=BIP-20DEC30-CDE")
    assert scoped.status_code == 200
    assert scoped.json()["payload"]["product_id"] == "BIP-20DEC30-CDE"
