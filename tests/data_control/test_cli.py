"""CLI tests for confirmation-gated market-data mutations."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.http_fakes import (
    matching_ready_payload,
    orchestration_status_payload,
    stale_ready_payload,
    urlopen_by_path,
    urlopen_ready_then,
)
from thytrader.data_control.cli import main


def test_data_help_describes_confirm_and_boundaries(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operators can discover the data CLI without an API."""
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "--confirm" in output
    assert "watch-add" in output
    assert "inspect-gaps" in output
    assert "not the operator" in output.lower() or "does not place orders" in output.lower()


def test_watch_add_help_lists_venue_clocks(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Watch-add help must name 1m, 2h, and 4h as complete-only dataset clocks."""
    with pytest.raises(SystemExit) as raised:
        main(["watch-add", "--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out.lower()
    collapsed = " ".join(output.split())
    assert "--timeframe" in collapsed
    assert "1m" in collapsed
    assert "2h" in collapsed
    assert "4h" in collapsed
    assert "per-indicator" in collapsed


def test_watch_add_without_confirm_does_not_mutate() -> None:
    """Omitting --confirm in Safe mode exits after the YOLO probe, before ingest HTTP."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(),
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch("thytrader.data_control.cli.add_watch") as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(["watch-add", "--product-id", "ETH-USD", "--timeframe", "5m"])
    assert raised.value.code != 0
    assert "Pass --confirm" in str(raised.value)
    request.assert_not_called()


def test_ingest_without_confirm_does_not_mutate() -> None:
    """Ingest is a mutation and requires --confirm unless YOLO covers data."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(),
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch("thytrader.data_control.cli.ingest") as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(["ingest", "--product-id", "ETH-USD", "--timeframe", "5m"])
    assert raised.value.code != 0
    assert "Pass --confirm" in str(raised.value)
    request.assert_not_called()


def test_fill_gaps_without_confirm_does_not_mutate() -> None:
    """Fill-gaps is a mutation and requires --confirm unless YOLO covers data."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(),
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch("thytrader.data_control.cli.fill_gaps") as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(["fill-gaps", "--product-id", "DOGE-USD", "--timeframe", "5m"])
    assert raised.value.code != 0
    assert "Pass --confirm" in str(raised.value)
    request.assert_not_called()


def test_watch_add_yolo_skips_confirm_and_mutates() -> None:
    """Data YOLO records a skip audit then proceeds without --confirm."""
    skip = {
        "id": "11111111-1111-1111-1111-111111111111",
        "category": "market_data",
        "action": "confirm_skipped",
        "outcome": "info",
        "tier": "data",
        "command": "watch-add",
    }
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(
            yolo_enabled=True,
            yolo_tiers=("data",),
        ),
        "POST /api/v1/agent-orchestration/skipped-confirmations": skip,
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch("thytrader.data_control.cli.add_watch", return_value={"ok": True}) as request,
    ):
        main(["watch-add", "--product-id", "ETH-USD", "--timeframe", "5m"])
    request.assert_called_once()


def test_data_cli_refuses_stale_ops_contract_before_command() -> None:
    """Every data command stops when the ready API does not match this checkout."""
    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(stale_ready_payload()),
        ),
        patch("thytrader.data_control.cli.list_watchlist") as request,
        pytest.raises(SystemExit, match="make run"),
    ):
        main(["watchlist-list"])
    request.assert_not_called()
