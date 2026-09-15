"""CLI tests for confirmation-gated paper and live runtime control."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.http_fakes import stale_ready_payload, urlopen_ready_then
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
    assert "not the operator or research CLI" in output.lower() or "not the operator" in output


def test_start_without_confirm_does_not_call_api() -> None:
    """Omitting --confirm must exit before HTTP."""
    with pytest.raises(SystemExit) as raised:
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


def test_set_risk_policy_without_confirm_does_not_call_api() -> None:
    """Risk-policy publication is a mutation and requires --confirm."""
    with pytest.raises(SystemExit) as raised:
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
