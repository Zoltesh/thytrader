"""CLI tests for thytrader-memory confirmation and YOLO hard-gate."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from tests.http_fakes import matching_ready_payload, stale_ready_payload, urlopen_by_path
from thytrader.memory.cli import main


def test_memory_help_describes_confirm_and_no_yolo(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operators can discover the memory lane without an API."""
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out.lower()
    assert "--confirm" in output
    assert "yolo never" in output or "not operator" in output


def test_add_journal_requires_confirm_even_if_yolo_on() -> None:
    """YOLO never covers memory mutations."""
    with pytest.raises(SystemExit, match="Pass --confirm"):
        main(
            [
                "add-journal",
                "--origin",
                "human",
                "--kind",
                "note",
                "--title",
                "Paused",
                "--body",
                "Stale candles.",
            ]
        )


def test_status_returns_memory_json(capsys: pytest.CaptureFixture[str]) -> None:
    """Status prints experiential-memory JSON after ops-contract preflight."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/memory": {
            "schema_version": "thytrader-experiential-memory-v1",
            "counts": {"journals": 0, "sentiment": 0, "patterns": 0, "notifications": 0},
            "notify_provider": "none",
            "notify_webhook_configured": False,
            "notify_enabled": False,
            "storage": "unavailable",
        },
    }
    with patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)):
        main(["status"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["notify_provider"] == "none"
    assert payload["notify_webhook_configured"] is False


def test_status_refuses_stale_ops_contract() -> None:
    """Memory CLI fails closed on a stale Compose image."""
    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_by_path({"GET /health/ready": stale_ready_payload()}),
        ),
        pytest.raises(SystemExit, match="make run"),
    ):
        main(["status"])
