"""Read-only ``thytrader-runtime decisions`` and ``thytrader-operator decisions`` CLIs."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from tests.http_fakes import json_urlopen_response, matching_ready_payload
from thytrader.operator.cli import main as operator_main
from thytrader.operator_chat.tools import split_request, tool_by_name
from thytrader.runtime_control.cli import main as runtime_main

if TYPE_CHECKING:
    from collections.abc import Callable

_DEPLOYMENT = "01985cf0-7b60-7000-8000-00000000d0d0"
_STRATEGY = "01985cf0-7b60-7000-8000-00000000c0de"
_PAGE = {
    "deployment_id": _DEPLOYMENT,
    "decisions": [],
    "limit": 2,
    "returned": 0,
    "next_cursor": None,
    "storage": "available",
}


def _recorder(payload: object, seen: list[str]) -> Callable[..., MagicMock]:
    """Return a urlopen double serving health then one JSON body, recording URLs."""

    def fake_urlopen(request: object, timeout: object = None) -> MagicMock:
        del timeout
        url = request if isinstance(request, str) else str(getattr(request, "full_url", ""))
        seen.append(url)
        if url.endswith("/health/ready"):
            return json_urlopen_response(matching_ready_payload())
        return json_urlopen_response(payload)

    return fake_urlopen


def test_runtime_decisions_reads_one_bot_page_with_repeated_outcomes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The command is read-only: no --confirm, one GET with repeated ``outcome``."""
    seen: list[str] = []
    with (
        patch("thytrader.agent_http.urlopen", side_effect=_recorder(_PAGE, seen)),
        pytest.raises(SystemExit) as raised,
    ):
        runtime_main(
            [
                "decisions",
                _DEPLOYMENT,
                "--outcome",
                "entry_signal",
                "--outcome",
                "exit",
                "--limit",
                "2",
            ]
        )
    assert raised.value.code == 0
    request = urlparse(seen[-1])
    assert request.path == f"/api/v1/deployments/{_DEPLOYMENT}/decisions"
    assert parse_qs(request.query) == {"limit": ["2"], "outcome": ["entry_signal", "exit"]}
    assert json.loads(capsys.readouterr().out)["storage"] == "available"


def test_runtime_decisions_by_strategy_narrows_to_one_bot_and_pages() -> None:
    """``--strategy-id`` reads the aggregated view; a deployment id narrows it."""
    seen: list[str] = []
    with (
        patch("thytrader.agent_http.urlopen", side_effect=_recorder(_PAGE, seen)),
        pytest.raises(SystemExit) as raised,
    ):
        runtime_main(["decisions", _DEPLOYMENT, "--strategy-id", _STRATEGY, "--cursor", "abc"])
    assert raised.value.code == 0
    request = urlparse(seen[-1])
    assert request.path == f"/api/v1/strategies/{_STRATEGY}/decisions"
    assert parse_qs(request.query) == {
        "limit": ["50"],
        "cursor": ["abc"],
        "deployment_id": [_DEPLOYMENT],
    }


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["decisions"], "Pass a deployment id or --strategy-id."),
        (["decisions", "not-a-uuid"], "The deployment id must be a UUID."),
        (["decisions", _DEPLOYMENT, "--limit", "0"], "--limit must be between 1 and 200."),
    ],
)
def test_runtime_decisions_validates_before_any_http(argv: list[str], message: str) -> None:
    """Bad ids or limits fail locally without touching the API."""
    with (
        patch("thytrader.agent_http.urlopen") as urlopen,
        pytest.raises(SystemExit) as raised,
    ):
        runtime_main(argv)
    assert message in str(raised.value)
    urlopen.assert_not_called()


def test_runtime_help_lists_the_read_only_decisions_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operators discover the timeline from --help alone."""
    with pytest.raises(SystemExit):
        runtime_main(["decisions", "--help"])
    output = capsys.readouterr().out
    assert "--strategy-id" in output
    assert "--outcome" in output
    assert "entry_blocked" in output
    assert "--cursor" in output


def test_operator_decisions_report_uses_repeated_outcome_query(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``thytrader-operator decisions`` fetches the v1 ``decisions`` report over HTTP."""
    report = {
        "schema_version": "thytrader-operator-report-v1",
        "application_version": "0.1.0",
        "generated_at": "2026-03-01T00:00:00Z",
        "timezone": "UTC",
        "overall_status": "healthy",
        "components": [
            {"name": "decisions", "status": "healthy", "reason_code": "OK", "detail": ""}
        ],
        "redaction": {
            "secrets_redacted": True,
            "raw_environment_omitted": True,
            "account_identifiers_omitted": True,
            "balances_omitted": True,
        },
        "partial_result_warnings": [],
        "recommended_next_action": "No action required.",
        "report_kind": "decisions",
        "payload": {
            "storage": "available",
            "deployment_id": _DEPLOYMENT,
            "strategy_id": None,
            "outcomes": ["entry_blocked"],
            "decisions": [],
            "next_cursor": None,
            "retention_max_rows_per_deployment": 20000,
            "retention_max_age_days": 180,
        },
    }
    seen: list[str] = []
    with (
        patch("thytrader.agent_http.urlopen", side_effect=_recorder(report, seen)),
        pytest.raises(SystemExit) as raised,
    ):
        operator_main(["decisions", "--deployment-id", _DEPLOYMENT, "--outcome", "entry_blocked"])
    assert raised.value.code == 0
    request = urlparse(seen[-1])
    assert request.path == "/api/v1/operator/decisions"
    assert parse_qs(request.query) == {
        "limit": ["50"],
        "deployment_id": [_DEPLOYMENT],
        "outcome": ["entry_blocked"],
    }
    assert json.loads(capsys.readouterr().out)["report_kind"] == "decisions"


def test_chat_tool_reads_one_bot_timeline_without_mutation() -> None:
    """The operator-chat catalog exposes a read-only decisions tool."""
    tool = tool_by_name("runtime_decisions")
    assert tool is not None
    assert tool.mutation is False
    assert tool.method == "GET"
    path, query, body = split_request(
        tool, {"deployment_id": _DEPLOYMENT, "outcome": "no_signal", "limit": 20}
    )
    assert path == f"/api/v1/deployments/{_DEPLOYMENT}/decisions"
    assert query == {"outcome": "no_signal", "limit": "20"}
    assert body is None
