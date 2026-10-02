"""Loopback HTTP client tests for agent CLIs."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from email.message import Message
import io
import json
import socket
import threading
from typing import Protocol
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from thytrader.agent_http import (
    AgentHttpError,
    default_api_base_url,
    request_json,
    require_loopback_base_url,
    require_matching_ops_contract,
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
from thytrader.ops_contract import expected_ops_contract


class _HasFullUrl(Protocol):
    """urllib Request-shaped object used by the patched urlopen helper."""

    full_url: str


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


@pytest.mark.parametrize(
    "url",
    (
        "http://127.0.0.1:8200/api/v1/operator/health",
        "http://127.0.0.1:8200/api/v1/data/watchlist",
        "http://127.0.0.1:8200/api/v1/strategies",
        "http://127.0.0.1:8200/api/v1/deployments",
    ),
)
def test_request_json_404_on_ready_api_hints_rebuild(url: str) -> None:
    """A stale Compose image that still answers /health/ready must tell operators to rebuild."""
    missing = HTTPError(url, 404, "Not Found", hdrs=Message(), fp=MagicMock())
    missing.read = MagicMock(return_value=b'{"detail":"Not Found"}')
    ready = MagicMock()
    ready.status = 200
    ready.read.return_value = b'{"status":"ready"}'
    ready.__enter__.return_value = ready
    ready.__exit__.return_value = None

    def fake_urlopen(request: _HasFullUrl | str, timeout: object = None) -> MagicMock:
        del timeout
        requested_url = request if isinstance(request, str) else request.full_url
        if requested_url.endswith("/health/ready"):
            return ready
        raise missing

    with (
        patch("thytrader.agent_http.urlopen", side_effect=fake_urlopen),
        pytest.raises(AgentHttpError, match="make run"),
    ):
        request_json(method="GET", url=url)


@pytest.mark.parametrize(
    "ready_payload",
    (
        {"status": "ready", "version": "0.1.0"},
        {
            "status": "ready",
            "version": "0.1.0",
            "ops_contract": {**expected_ops_contract(), "id": "stale-contract"},
        },
    ),
)
def test_require_matching_ops_contract_rejects_healthy_stale_api(
    ready_payload: dict[str, object],
) -> None:
    """Healthy 0.1.0 is not current when the full ops contract is absent or unequal."""
    with (
        patch("thytrader.agent_http.request_json", return_value=ready_payload),
        pytest.raises(AgentHttpError, match="make run"),
    ):
        require_matching_ops_contract("http://127.0.0.1:8200")


def test_require_matching_ops_contract_accepts_exact_contract() -> None:
    """The shared preflight accepts the exact contract advertised by this checkout."""
    payload = {
        "status": "ready",
        "version": "0.1.0",
        "ops_contract": expected_ops_contract(),
    }
    with patch("thytrader.agent_http.request_json", return_value=payload):
        require_matching_ops_contract("http://127.0.0.1:8200")


def test_default_api_base_url_is_loopback() -> None:
    """Settings without an override still produce a loopback origin."""
    settings = Settings(_env_file=None)
    assert default_api_base_url(settings).startswith("http://127.0.0.1:")


def _stalling_loopback_server() -> tuple[socket.socket, threading.Event]:
    """Accept loopback connections and never answer, like an API stuck on a long request."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    done = threading.Event()

    def hold_connections() -> None:
        held: list[socket.socket] = []
        server.settimeout(0.1)
        while not done.is_set():
            try:
                connection, _address = server.accept()
            except OSError:
                continue
            held.append(connection)
        for connection in held:
            connection.close()

    threading.Thread(target=hold_connections, daemon=True).start()
    return server, done


def test_request_json_reports_a_read_timeout_instead_of_escaping() -> None:
    """A request the API accepted but did not answer is a clear, flagged AgentHttpError."""
    server, done = _stalling_loopback_server()
    port = server.getsockname()[1]
    try:
        with pytest.raises(AgentHttpError) as raised:
            request_json(
                method="POST",
                url=f"http://127.0.0.1:{port}/api/v1/backtests",
                payload={},
                timeout=0.2,
            )
    finally:
        done.set()
        server.close()
    message = str(raised.value)
    assert raised.value.timed_out is True
    assert raised.value.status is None
    assert "Timed out after 0.2 s waiting for the ThyTrader API to answer POST" in message
    assert "/api/v1/backtests" in message
    assert "may still be running on the server" in message


def test_request_json_reports_a_connect_timeout_without_calling_the_api_down() -> None:
    """A connect-phase timeout is not "unreachable": the API may be busy, nothing was sent."""
    with (
        patch("thytrader.agent_http.urlopen", side_effect=URLError(TimeoutError("timed out"))),
        pytest.raises(AgentHttpError) as raised,
    ):
        request_json(
            method="GET", url="http://127.0.0.1:8200/api/v1/operator/data-catalog", timeout=30.0
        )
    message = str(raised.value)
    assert raised.value.timed_out is True
    assert "Timed out after 30 s waiting to connect" in message
    assert "was not sent" in message
    assert "unreachable" not in message


def _closing_loopback_server() -> tuple[socket.socket, threading.Event]:
    """Accept loopback connections, read the request, and close without answering."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    done = threading.Event()

    def drop_connections() -> None:
        server.settimeout(0.1)
        while not done.is_set():
            try:
                connection, _address = server.accept()
            except OSError:
                continue
            connection.settimeout(1.0)
            with contextlib.suppress(OSError):
                connection.recv(65_536)
            connection.close()

    threading.Thread(target=drop_connections, daemon=True).start()
    return server, done


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("GET", "Nothing was changed; retry the read."),
        ("POST", "check state before repeating the mutation"),
    ],
)
def test_request_json_reports_a_dropped_connection_instead_of_escaping(
    method: str, expected: str
) -> None:
    """A server that closes before answering is a flagged AgentHttpError, not a bare error."""
    server, done = _closing_loopback_server()
    port = server.getsockname()[1]
    try:
        with pytest.raises(AgentHttpError) as raised:
            request_json(
                method=method,
                url=f"http://127.0.0.1:{port}/api/v1/research/jobs/abc",
                payload=None if method == "GET" else {},
                timeout=2.0,
            )
    finally:
        done.set()
        server.close()
    message = str(raised.value)
    assert raised.value.dropped is True
    assert raised.value.timed_out is False
    assert raised.value.status is None
    assert f"closed the connection before answering {method} /api/v1/research/jobs/abc" in message
    assert expected in message


def test_request_json_maps_a_connection_reset_to_a_dropped_error() -> None:
    """Resets raised outside urllib's URLError wrapping still say what failed."""
    with (
        patch("thytrader.agent_http.urlopen", side_effect=ConnectionResetError(104, "reset")),
        pytest.raises(AgentHttpError) as raised,
    ):
        request_json(method="GET", url="http://127.0.0.1:8200/api/v1/backtests/sha256:abc")
    assert raised.value.dropped is True
    assert "ConnectionResetError" in str(raised.value)


def _http_error(status: int, body: object) -> HTTPError:
    """Build one urllib HTTPError carrying a JSON body."""
    return HTTPError(
        "http://127.0.0.1:8200/api/v1/research/studies/plan",
        status,
        "error",
        Message(),
        io.BytesIO(json.dumps(body).encode("utf-8")),
    )


def test_request_json_keeps_detail_code_and_message() -> None:
    """A structured 4xx detail prints its code and full message, not a truncated body."""
    message_text = "evaluation_start requires warmup coverage. " + ("x" * 700) + " Suggested range."
    body = {"detail": {"code": "study_window_rejected", "message": message_text}}
    with (
        patch("thytrader.agent_http.urlopen", side_effect=_http_error(422, body)),
        pytest.raises(AgentHttpError) as raised,
    ):
        request_json(method="POST", url="http://127.0.0.1:8200/api/v1/research/studies/plan")
    message = str(raised.value)
    assert raised.value.status == 422
    assert raised.value.code == "study_window_rejected"
    assert message.startswith("HTTP 422 study_window_rejected: evaluation_start requires warmup")
    assert message.endswith("Suggested range.")
    assert "{" not in message


def test_request_json_renders_request_validation_lists_as_fields() -> None:
    """FastAPI request-validation errors name the field instead of dumping JSON."""
    body = {
        "detail": [
            {"type": "missing", "loc": ["body", "initial_quote_balance"], "msg": "Field required"},
            {"type": "missing", "loc": ["body", "maker_fee_rate"], "msg": "Field required"},
        ]
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=_http_error(422, body)),
        pytest.raises(AgentHttpError) as raised,
    ):
        request_json(method="POST", url="http://127.0.0.1:8200/api/v1/backtests")
    assert str(raised.value) == (
        "HTTP 422: initial_quote_balance: Field required; maker_fee_rate: Field required"
    )
    assert raised.value.code is None


@pytest.mark.parametrize(
    ("method", "hint"),
    [("GET", "retry shortly"), ("POST", "check state before retrying")],
)
def test_request_json_server_errors_say_what_to_do_next(method: str, hint: str) -> None:
    """A 5xx names the failure and the next step, distinguishing reads from mutations."""
    body = {"detail": "Research study planning is unavailable."}
    with (
        patch("thytrader.agent_http.urlopen", side_effect=_http_error(503, body)),
        pytest.raises(AgentHttpError) as raised,
    ):
        request_json(method=method, url="http://127.0.0.1:8200/api/v1/research/studies/plan")
    message = str(raised.value)
    assert message.startswith("HTTP 503: Research study planning is unavailable.")
    assert hint in message
    assert "thytrader-operator health" in message


def test_request_json_unreachable_names_the_base_url_resolution() -> None:
    """Connection refused explains where the CLI looked and how to point it elsewhere."""
    refused = URLError(ConnectionRefusedError(111, "Connection refused"))
    with (
        patch("thytrader.agent_http.urlopen", side_effect=refused),
        pytest.raises(AgentHttpError) as raised,
    ):
        request_json(method="GET", url="http://127.0.0.1:8000/api/v1/operator/health")
    message = str(raised.value)
    assert "unreachable at http://127.0.0.1:8000 (Connection refused)" in message
    assert "THYTRADER_API_BASE_URL" in message
    assert "THYTRADER_API_PORT" in message
