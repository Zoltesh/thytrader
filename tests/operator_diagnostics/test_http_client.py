"""Loopback HTTP client tests for agent CLIs."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from unittest.mock import MagicMock, patch
from urllib.error import URLError

import pytest

from thytrader.agent_http import (
    AgentHttpError,
    default_api_base_url,
    request_json,
    require_loopback_base_url,
)
from thytrader.config import Settings
from thytrader.operator.http import fetch_operator_report
from thytrader.operator.models import (
    SCHEMA_VERSION,
    STANDARD_REDACTION,
    HealthPayload,
    HealthReport,
    ReportStatus,
)


def test_require_loopback_base_url_accepts_localhost() -> None:
    """Loopback origins are normalized to scheme plus host and port."""
    assert require_loopback_base_url("http://127.0.0.1:8200/") == "http://127.0.0.1:8200"
    assert require_loopback_base_url("http://localhost:8200") == "http://localhost:8200"


def test_require_loopback_base_url_rejects_remote() -> None:
    """Remote hosts are not a supported agent transport."""
    with pytest.raises(AgentHttpError, match="loopback"):
        require_loopback_base_url("http://example.com:8200")


def test_request_json_does_not_fall_back_when_api_is_down() -> None:
    """Connection failures stay HTTP failures."""
    with (
        patch("thytrader.agent_http.urlopen", side_effect=URLError("down")),
        pytest.raises(AgentHttpError, match="unreachable"),
    ):
        request_json(method="GET", url="http://127.0.0.1:1/api/v1/operator/health")


def test_fetch_operator_report_validates_health_envelope() -> None:
    """HTTP payloads must match the versioned HealthReport model."""
    payload = HealthReport(
        application_version="0.1.0",
        generated_at=datetime.now(UTC),
        overall_status=ReportStatus.DEGRADED,
        components=(),
        redaction=STANDARD_REDACTION,
        recommended_next_action="Start thytrader-api on the configured loopback port and retry.",
        payload=HealthPayload(
            api_probed=True,
            database_configured=False,
            coinbase_credentials_configured=False,
        ),
    ).model_dump(mode="json")
    raw = json.dumps(payload).encode("utf-8")
    response = MagicMock()
    response.status = 200
    response.read.return_value = raw
    response.__enter__.return_value = response
    response.__exit__.return_value = None
    with patch("thytrader.agent_http.urlopen", return_value=response):
        report = fetch_operator_report(base_url="http://127.0.0.1:8200", command="health")
    assert isinstance(report, HealthReport)
    assert report.schema_version == SCHEMA_VERSION
    assert report.report_kind == "health"


def test_default_api_base_url_is_loopback() -> None:
    """Settings without an override still produce a loopback origin."""
    settings = Settings(_env_file=None)
    assert default_api_base_url(settings).startswith("http://127.0.0.1:")
