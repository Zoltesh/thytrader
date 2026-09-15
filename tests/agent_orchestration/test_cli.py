"""CLI tests for the playbook orchestrator."""

from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest

from tests.http_fakes import (
    matching_ready_payload,
    orchestration_status_payload,
    stale_ready_payload,
    urlopen_by_path,
    urlopen_ready_then,
)
from thytrader.agent_orchestration.cli import main
from thytrader.operator.status import EXIT_HEALTHY


def test_playbook_help_describes_confirm_and_no_live(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operators can discover the playbook without an API."""
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out.lower()
    assert "--confirm" in output
    assert "cannot start live" in output or "not the operator" in output
    assert "yolo" in output


def test_status_returns_safe_mode_by_default(capsys: pytest.CaptureFixture[str]) -> None:
    """Status prints orchestration JSON and never claims live authority."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(),
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        pytest.raises(SystemExit) as raised,
    ):
        main(["status"])
    assert raised.value.code == EXIT_HEALTHY
    payload = json.loads(capsys.readouterr().out)
    assert payload["confirmation_mode"] == "safe"
    assert payload["live_hard_gate"] is True
    assert payload["live_authority"] is False
    assert payload["live_started"] is False
    assert payload["cli"] == "thytrader-playbook"


def test_status_refuses_stale_ops_contract() -> None:
    """Playbook status fails closed on a stale Compose image."""
    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(stale_ready_payload()),
        ),
        pytest.raises(SystemExit, match="make run"),
    ):
        main(["status"])


def test_run_sequences_operator_and_data_clis(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run calls existing CLIs and never constructs a live start."""
    calls: list[tuple[str, list[str]]] = []

    def fake_operator(argv: list[str] | None) -> None:
        assert argv is not None
        calls.append(("operator", list(argv)))
        raise SystemExit(0)

    def fake_data(argv: list[str] | None) -> None:
        assert argv is not None
        calls.append(("data", list(argv)))
        sys.stdout.write('{"targets":[]}\n')

    monkeypatch.setattr("thytrader.agent_orchestration.cli.operator_main", fake_operator)
    monkeypatch.setattr("thytrader.agent_orchestration.cli.data_main", fake_data)
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(),
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        pytest.raises(SystemExit) as raised,
    ):
        main(["run", "--product-id", "ETH-USD", "--timeframe", "5m"])
    assert raised.value.code == EXIT_HEALTHY
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "thytrader-playbook-run-v1"
    assert payload["live_started"] is False
    assert payload["live_authority"] is False
    assert payload["live_hard_gate"] is True
    assert calls[0][0] == "operator"
    assert calls[0][1] == ["health"]
    assert calls[1][0] == "data"
    assert "watchlist-list" in calls[1][1]
    assert not any("--mode" in argv and "live" in argv for _, argv in calls)


def test_run_create_draft_forwards_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Playbook forwards --confirm to research instead of inventing HTTP."""
    research_calls: list[list[str]] = []

    def fake_operator(argv: list[str] | None) -> None:
        del argv
        raise SystemExit(0)

    def fake_data(argv: list[str] | None) -> None:
        del argv
        sys.stdout.write('{"targets":[]}\n')

    def fake_research(argv: list[str] | None) -> None:
        assert argv is not None
        research_calls.append(list(argv))
        sys.stdout.write('{"strategy_id":"11111111-1111-1111-1111-111111111111"}\n')
        raise SystemExit(0)

    monkeypatch.setattr("thytrader.agent_orchestration.cli.operator_main", fake_operator)
    monkeypatch.setattr("thytrader.agent_orchestration.cli.data_main", fake_data)
    monkeypatch.setattr("thytrader.agent_orchestration.cli.research_main", fake_research)
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(),
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch("thytrader.agent_orchestration.cli.sys.stdout"),
        pytest.raises(SystemExit) as raised,
    ):
        main(["run", "--create-draft", "--confirm"])
    assert raised.value.code == EXIT_HEALTHY
    assert research_calls
    assert "--confirm" in research_calls[0]
    assert "create-draft" in research_calls[0]
    assert "live" not in research_calls[0]


def test_run_paper_uses_runtime_paper_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Optional paper start is always --mode paper."""
    runtime_calls: list[list[str]] = []

    def fake_operator(argv: list[str] | None) -> None:
        del argv
        raise SystemExit(0)

    def fake_data(argv: list[str] | None) -> None:
        del argv
        sys.stdout.write('{"targets":[]}\n')

    def fake_runtime(argv: list[str] | None) -> None:
        assert argv is not None
        runtime_calls.append(list(argv))
        sys.stdout.write('{"id":"dep-1","mode":"paper","status":"running"}\n')
        raise SystemExit(0)

    monkeypatch.setattr("thytrader.agent_orchestration.cli.operator_main", fake_operator)
    monkeypatch.setattr("thytrader.agent_orchestration.cli.data_main", fake_data)
    monkeypatch.setattr("thytrader.agent_orchestration.cli.runtime_main", fake_runtime)
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(),
    }
    fingerprint = "sha256:" + "a" * 64
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch("thytrader.agent_orchestration.cli.sys.stdout"),
        pytest.raises(SystemExit) as raised,
    ):
        main(
            [
                "run",
                "--paper-cash",
                "10000",
                "--strategy-fingerprint",
                fingerprint,
                "--confirm",
            ]
        )
    assert raised.value.code == EXIT_HEALTHY
    assert "--mode" in runtime_calls[0]
    mode_index = runtime_calls[0].index("--mode")
    assert runtime_calls[0][mode_index + 1] == "paper"
    assert "live" not in runtime_calls[0]
    assert "--i-understand-live" not in runtime_calls[0]
