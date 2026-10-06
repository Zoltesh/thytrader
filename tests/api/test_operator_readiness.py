"""HTTP contract for the read-only readiness and venue-reconciliation reports."""

from __future__ import annotations

from fastapi.testclient import TestClient

from thytrader.api.app import create_app
from thytrader.config import Settings
from thytrader.operator.models import SCHEMA_VERSION


def test_readiness_and_venue_reconciliation_are_get_only() -> None:
    """Both reports return the v1 envelope and reject mutations."""
    app = create_app(Settings(_env_file=None))
    with TestClient(app) as client:
        for path, kind in (
            ("/api/v1/operator/readiness", "readiness"),
            ("/api/v1/operator/venue-reconciliation", "venue_reconciliation"),
        ):
            response = client.get(path)
            assert response.status_code == 200, path
            payload = response.json()
            assert payload["schema_version"] == SCHEMA_VERSION
            assert payload["report_kind"] == kind
            assert payload["timezone"] == "UTC"
            assert payload["redaction"]["secrets_redacted"] is True
            assert payload["redaction"]["account_identifiers_omitted"] is True
            assert "api_key" not in response.text
            assert client.post(path).status_code in {404, 405, 422}
