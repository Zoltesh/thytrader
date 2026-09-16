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


def test_live_start_without_ack_does_not_probe_yolo() -> None:
    """`--i-understand-live` is checked before YOLO and is never skipped."""
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
    assert "--i-understand-live" in str(raised.value)
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


def test_live_start_without_confirm_fails_closed_when_yolo_off() -> None:
    """Live YOLO is fail-closed: ack without `--confirm` still needs the live tier."""
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
                "live",
                "--i-understand-live",
            ]
        )
    assert raised.value.code != 0
    assert "Pass --confirm" in str(raised.value)
    request.assert_not_called()


def test_live_start_yolo_skips_confirm() -> None:
    """Live YOLO records a skip then starts without `--confirm` when ack is present."""
    skip = {
        "id": "11111111-1111-1111-1111-111111111111",
        "category": "runtime",
        "action": "confirm_skipped",
        "outcome": "info",
        "tier": "live",
        "command": "start",
    }
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(
            yolo_enabled=True,
            yolo_tiers=("live",),
        ),
        "POST /api/v1/agent-orchestration/skipped-confirmations": skip,
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch(
            "thytrader.runtime_control.cli.start_deployment",
            return_value={"id": "dep", "mode": "live"},
        ) as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(
            [
                "start",
                "--strategy-fingerprint",
                "sha256:" + "a" * 64,
                "--mode",
                "live",
                "--i-understand-live",
            ]
        )
    assert raised.value.code == 0
    request.assert_called_once()


def test_paper_yolo_does_not_skip_live_start() -> None:
    """Paper YOLO plus `--i-understand-live` must not arm live without `--confirm`."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(
            yolo_enabled=True,
            yolo_tiers=("paper",),
        ),
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
                "live",
                "--i-understand-live",
            ]
        )
    assert raised.value.code != 0
    assert "Pass --confirm" in str(raised.value)
    request.assert_not_called()


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


def test_live_yolo_skips_live_pause() -> None:
    """Live YOLO records a skip then pauses a live deployment without `--confirm`."""
    skip = {
        "id": "11111111-1111-1111-1111-111111111111",
        "category": "runtime",
        "action": "confirm_skipped",
        "outcome": "info",
        "tier": "live",
        "command": "pause",
    }
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(
            yolo_enabled=True,
            yolo_tiers=("live",),
        ),
        "POST /api/v1/agent-orchestration/skipped-confirmations": skip,
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch(
            "thytrader.runtime_control.cli.show_deployment",
            return_value={"id": "dep", "mode": "live"},
        ),
        patch(
            "thytrader.runtime_control.cli.set_deployment_status",
            return_value={"id": "dep", "mode": "live", "status": "paused"},
        ) as request,
        pytest.raises(SystemExit) as raised,
    ):
        main(["pause", "11111111-1111-1111-1111-111111111111"])
    assert raised.value.code == 0
    request.assert_called_once()


def test_live_yolo_does_not_skip_paper_pause() -> None:
    """Live YOLO must not skip confirmation on a paper deployment."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(
            yolo_enabled=True,
            yolo_tiers=("live",),
        ),
    }
    with (
        patch("thytrader.agent_http.urlopen", side_effect=urlopen_by_path(handlers)),
        patch(
            "thytrader.runtime_control.cli.show_deployment",
            return_value={"id": "dep", "mode": "paper"},
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


def test_set_risk_policy_help_lists_breaker_flags(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operators can discover daily-loss, drawdown, rate, and collar flags."""
    with pytest.raises(SystemExit) as raised:
        main(["set-risk-policy", "--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "--daily-loss-limit-fraction" in output
    assert "--max-strategy-drawdown-fraction" in output
    assert "--max-entry-orders-per-minute" in output
    assert "--max-cancellations-per-minute" in output
    assert "--reference-price-collar-fraction" in output


def test_place_order_help_lists_venue_clocks(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Place-order help must name ingested venue clocks for the discretionary book."""
    with pytest.raises(SystemExit) as raised:
        main(["place-order", "--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out.lower()
    collapsed = " ".join(output.split())
    assert "--timeframe" in collapsed
    assert "1m" in collapsed
    assert "2h" in collapsed
    assert "4h" in collapsed
    assert "discretionary book clock" in collapsed
    assert "--side" in collapsed
    assert "long" in collapsed
    assert "short" in collapsed


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


def test_live_yolo_does_not_skip_live_place_order() -> None:
    """Live place-order stays confirmation-hard-gated even when YOLO live is on."""
    with (
        patch("thytrader.agent_http.urlopen") as urlopen,
        patch("thytrader.runtime_control.cli.place_discretionary_order") as request,
        pytest.raises(SystemExit) as raised,
    ):
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
                "--i-understand-live",
            ]
        )
    assert raised.value.code != 0
    assert "Pass --confirm" in str(raised.value)
    urlopen.assert_not_called()
    request.assert_not_called()


def test_place_order_forwards_short_side() -> None:
    """`--side short` is composed onto the HTTP client without rewriting YOLO gates."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(),
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
                "--side",
                "short",
                "--stop-price",
                "200000",
                "--take-profit-price",
                "50000",
                "--quantity",
                "0.01",
                "--idempotency-key",
                "k-short",
                "--cash",
                "10000",
                "--confirm",
            ]
        )
    assert raised.value.code == 0
    assert request.call_args.kwargs["side"] == "short"


def test_start_help_lists_paper_fee_flags(capsys: pytest.CaptureFixture[str]) -> None:
    """Operators can discover paper maker/taker assumptions without an API."""
    with pytest.raises(SystemExit) as raised:
        main(["start", "--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "--maker-fee-rate" in output
    assert "--taker-fee-rate" in output
    assert "0.001" in output
    assert "Live rejects" in output or "live rejects" in output.lower()


def test_paper_start_forwards_fee_rates() -> None:
    """Paper start sends optional documented fee assumptions to the HTTP helper."""
    handlers = {
        "GET /health/ready": matching_ready_payload(),
        "GET /api/v1/agent-orchestration": orchestration_status_payload(),
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
                "--maker-fee-rate",
                "0.0025",
                "--taker-fee-rate",
                "0.004",
                "--confirm",
            ]
        )
    assert raised.value.code == 0
    assert request.call_args.kwargs["maker_fee_rate"] == "0.0025"
    assert request.call_args.kwargs["taker_fee_rate"] == "0.004"


def test_paper_start_rejects_one_sided_fee_flags() -> None:
    """Maker and taker flags must be supplied together."""
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
                "--maker-fee-rate",
                "0.0025",
                "--confirm",
            ]
        )
    assert raised.value.code != 0
    assert "--maker-fee-rate" in str(raised.value)
    request.assert_not_called()


def test_live_start_rejects_paper_fee_flags() -> None:
    """Live never accepts modeled paper fee rates."""
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
                "live",
                "--maker-fee-rate",
                "0.001",
                "--taker-fee-rate",
                "0.002",
                "--confirm",
                "--i-understand-live",
            ]
        )
    assert raised.value.code != 0
    assert "paper fee" in str(raised.value).lower()
    request.assert_not_called()
