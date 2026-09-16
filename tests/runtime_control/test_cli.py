"""CLI tests for confirmation-gated paper and live runtime control."""

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
from thytrader.runtime_control.cli import main


def test_runtime_help_describes_confirm_and_live_ack(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operators can discover the live acknowledgement gate without an API."""
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "--confirm" in output
    assert "--i-understand-live" in output
    assert "not the operator" in output.lower() or "risk-policy registry" in output.lower()


def test_start_without_confirm_does_not_call_api() -> None:
    """Omitting --confirm in Safe mode exits after the YOLO probe, before start HTTP."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(),
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch("thytrader.runtime_control.cli.start_deployment") as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(
            [
                "start",
                "--strategy-fingerprint",
                "sha256:" + "a" * 64,
                "--mode",
                "paper",
                "--cash",
                "10000",
            ]
        )
    assert raised.value.code != 0
    assert "Pass --confirm" in str(raised.value)
    request.assert_not_called()


def test_live_start_without_confirm_does_not_probe_yolo() -> None:
    """Live start is a hard gate and must not consult YOLO."""
    with (
        patch("thytrader.agent_http.urlopen") as urlopen,
        patch("thytrader.runtime_control.cli.start_deployment") as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(
            [
                "start",
                "--strategy-fingerprint",
                "sha256:" + "a" * 64,
                "--mode",
                "live",
            ]
        )
    assert raised.value.code != 0
    assert "Pass --confirm" in str(raised.value)
    urlopen.assert_not_called()
    request.assert_not_called()


def test_live_start_requires_dedicated_ack() -> None:
    """--confirm alone is not enough to arm live trading."""
    with pytest.raises(SystemExit) as raised:
        main(
            [
                "start",
                "--strategy-fingerprint",
                "sha256:" + "a" * 64,
                "--mode",
                "live",
                "--confirm",
            ]
        )
    assert raised.value.code != 0
    assert "--i-understand-live" in str(raised.value)


def test_paper_start_requires_cash() -> None:
    """Paper deployments need an explicit starting-cash decimal string."""
    with pytest.raises(SystemExit) as raised:
        main(
            [
                "start",
                "--strategy-fingerprint",
                "sha256:" + "a" * 64,
                "--mode",
                "paper",
                "--confirm",
            ]
        )
    assert raised.value.code != 0
    assert "--cash" in str(raised.value)


def test_paper_start_yolo_skips_confirm() -> None:
    """Paper YOLO records a skip then starts without --confirm. Live stays gated."""
    skip = {
        "id": "11111111-1111-1111-1111-111111111111",
        "category": "runtime",
        "action": "confirm_skipped",
        "outcome": "info",
        "tier": "paper",
        "command": "start",
    }
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(
            yolo_enabled=True,
            yolo_tiers=("paper",),
        ),
        "POST /api/v1/agent-orchestration/skipped-confirmations": skip,
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch(
            "thytrader.runtime_control.cli.start_deployment",
            return_value={"id": "dep", "mode": "paper"},
        ) as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(
            [
                "start",
                "--strategy-fingerprint",
                "sha256:" + "a" * 64,
                "--mode",
                "paper",
                "--cash",
                "10000",
            ]
        )
    assert raised.value.code == 0
    request.assert_called_once()


def test_runtime_cli_refuses_stale_ops_contract_before_command() -> None:
    """Every runtime command stops when the ready API does not match this checkout."""
    with (
        patch(
            "thytrader.agent_http.urlopen",
            side_effect=urlopen_ready_then(stale_ready_payload()),
        ),
        patch("thytrader.runtime_control.cli.list_deployments") as request,
        pytest.raises(SystemExit, match="make run"),
    ):
        main(["list"])
    request.assert_not_called()


def test_set_risk_policy_without_confirm_does_not_probe_yolo() -> None:
    """Risk-policy publication is a hard gate and never consults YOLO."""
    with (
        patch("thytrader.agent_http.urlopen") as urlopen,
        patch("thytrader.runtime_control.cli.set_risk_policy") as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(
            [
                "set-risk-policy",
                "--max-concurrent-running-deployments",
                "8",
                "--max-concurrent-open-positions",
                "8",
                "--max-portfolio-exposure-fraction",
                "1",
                "--per-product-max-exposure-fraction",
                "1",
                "--paper-capital-quote",
                "100000",
            ]
        )
    assert raised.value.code != 0
    assert "Pass --confirm" in str(raised.value)
    urlopen.assert_not_called()
    request.assert_not_called()


def test_paper_yolo_does_not_skip_live_pause() -> None:
    """Paper YOLO must not skip confirmation on a live deployment."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(
            yolo_enabled=True,
            yolo_tiers=("paper",),
        ),
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch(
            "thytrader.runtime_control.cli.show_deployment",
            return_value={"id": "dep", "mode": "live"},
        ),
        patch("thytrader.runtime_control.cli.set_deployment_status") as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(["pause", "11111111-1111-1111-1111-111111111111"])
    assert raised.value.code != 0
    assert "Pass --confirm" in str(raised.value)
    request.assert_not_called()


def test_runtime_help_lists_risk_policy_commands(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operators can discover show-risk-policy and set-risk-policy without an API."""
    with pytest.raises(SystemExit) as raised:
        main(["--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "show-risk-policy" in output
    assert "set-risk-policy" in output
    assert "place-order" in output


def test_place_order_without_confirm_does_not_call_api() -> None:
    """Omitting --confirm in Safe mode exits before place-order HTTP."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(),
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch("thytrader.runtime_control.cli.place_discretionary_order") as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(
            [
                "place-order",
                "--mode",
                "paper",
                "--product-id",
                "BTC-USD",
                "--stop-price",
                "50000",
                "--take-profit-price",
                "200000",
                "--quantity",
                "0.01",
                "--idempotency-key",
                "k1",
                "--cash",
                "10000",
            ]
        )
    assert raised.value.code != 0
    assert "Pass --confirm" in str(raised.value)
    request.assert_not_called()


def test_live_place_order_requires_dedicated_ack() -> None:
    """--confirm alone is not enough to place a live discretionary order."""
    with pytest.raises(SystemExit) as raised:
        main(
            [
                "place-order",
                "--mode",
                "live",
                "--product-id",
                "BTC-USD",
                "--stop-price",
                "50000",
                "--take-profit-price",
                "200000",
                "--quantity",
                "0.01",
                "--idempotency-key",
                "k1",
                "--confirm",
            ]
        )
    assert raised.value.code != 0
    assert "--i-understand-live" in str(raised.value)


def test_paper_place_order_yolo_skips_confirm() -> None:
    """Paper YOLO records a skip then places without --confirm. Live stays gated."""
    skip = {
        "id": "11111111-1111-1111-1111-111111111111",
        "category": "runtime",
        "action": "confirm_skipped",
        "outcome": "info",
        "tier": "paper",
        "command": "place-order",
    }
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(
            yolo_enabled=True,
            yolo_tiers=("paper",),
        ),
        "POST /api/v1/agent-orchestration/skipped-confirmations": skip,
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch(
            "thytrader.runtime_control.cli.place_discretionary_order",
            return_value={"id": "dep", "mode": "paper", "kind": "discretionary"},
        ) as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(
            [
                "place-order",
                "--mode",
                "paper",
                "--product-id",
                "BTC-USD",
                "--stop-price",
                "50000",
                "--take-profit-price",
                "200000",
                "--quantity",
                "0.01",
                "--idempotency-key",
                "k1",
                "--cash",
                "10000",
            ]
        )
    assert raised.value.code == 0
    request.assert_called_once()
