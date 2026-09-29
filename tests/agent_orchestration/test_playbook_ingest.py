"""Playbook ingest returns after the 202 unless later steps in the same run need the data."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from tests.http_fakes import matching_ready_payload, orchestration_status_payload, urlopen_by_path
from thytrader.agent_orchestration.cli import main


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> list[list[str]]:
    """Run the playbook with fake child CLIs and return the data CLI argv calls."""
    data_calls: list[list[str]] = []

    def fake_operator(child: list[str] | None) -> None:
        del child
        raise SystemExit(0)

    def fake_data(child: list[str] | None) -> None:
        assert child is not None
        data_calls.append(list(child))
        sys.stdout.write('{"targets":[]}\n')

    def fake_research(child: list[str] | None) -> None:
        del child
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
        pytest.raises(SystemExit),
    ):
        main(argv)
    return data_calls


def _ingest_call(calls: list[list[str]]) -> list[str]:
    """Return the single forwarded ingest argv."""
    matches = [call for call in calls if "ingest" in call]
    assert len(matches) == 1
    return matches[0]


def test_ingest_only_run_uses_no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ingest-only playbook run must not block up to 45 minutes."""
    calls = _run(
        monkeypatch,
        ["run", "--product-id", "BTC-USDC", "--timeframe", "5m", "--ingest", "--confirm"],
    )
    ingest = _ingest_call(calls)
    assert "--no-wait" in ingest
    assert "--confirm" in ingest


def test_ingest_followed_by_research_waits_for_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the same run drafts next, ingest keeps waiting so research sees the data."""
    calls = _run(
        monkeypatch,
        [
            "run",
            "--product-id",
            "BTC-USDC",
            "--timeframe",
            "5m",
            "--ingest",
            "--create-draft",
            "--confirm",
        ],
    )
    assert "--no-wait" not in _ingest_call(calls)
